use super::{
    SailCore, SailCoreFault, SailCoreRequest, SailCoreResponse, SailCoreStatus,
    SailCoreValidationRange,
    protocol::{request_kind, request_role, response_kind, transaction_access},
};
use crate::translation::{
    self, MIN_PAGE_BYTES, TranslationAccess, TranslationError, TranslationFault, TranslationResult,
};
use bedrock_bus::{Bus, BusError, PhysicalMemoryClass};
use std::collections::BTreeMap;
use std::fmt;

#[derive(Debug)]
pub enum SailBusExecutionError {
    Core(SailCoreStatus),
    Fault {
        fault: SailCoreFault,
        request: Option<Box<SailCoreRequest>>,
    },
    Bus(BusError),
    UnsupportedRequest(i32),
    UnsupportedNumericOperation(i32),
    InvalidFraming {
        first: u8,
        second: Option<u8>,
    },
}

impl fmt::Display for SailBusExecutionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Core(status) => write!(formatter, "Sail core returned {status:?}"),
            Self::Fault { fault, .. } => {
                write!(formatter, "Sail execution fault: {fault:?}")
            }
            Self::Bus(error) => error.fmt(formatter),
            Self::UnsupportedRequest(kind) => {
                write!(
                    formatter,
                    "unsupported Sail environment request kind {kind}"
                )
            }
            Self::UnsupportedNumericOperation(operation) => {
                write!(formatter, "unsupported Sail numeric operation {operation}")
            }
            Self::InvalidFraming { first, second } => write!(
                formatter,
                "invalid instruction framing bytes {first:#04x}, {second:?}"
            ),
        }
    }
}

impl std::error::Error for SailBusExecutionError {}

impl From<BusError> for SailBusExecutionError {
    fn from(error: BusError) -> Self {
        Self::Bus(error)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SailBusCompletion {
    Retired,
    VectorLane,
    DebugStop,
}

#[derive(Debug, Clone, Copy)]
struct RequestTranslation {
    ptcr: u64,
    access: TranslationAccess,
    user_domain: bool,
    supervisor: bool,
    monitoring_enabled: bool,
    first: TranslationResult,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct LogicalMemoryRange {
    linear_address: u64,
    buffer_offset: usize,
    length: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct PreparedMemoryRange {
    physical_address: u64,
    buffer_offset: usize,
    length: usize,
    fault_range: SailCoreValidationRange,
}

#[derive(Debug)]
enum PrepareMemoryRangesError {
    Invalid(BusError),
    Fault(TranslationFault, u64),
    Bus(BusError),
}

impl SailCore {
    /// Fetches and executes one instruction at the current PC.
    pub fn step_on_bus(&mut self, bus: &mut impl Bus) -> Result<(), SailBusExecutionError> {
        let monitoring_enabled =
            self.control(0x1100).map_err(SailBusExecutionError::Core)? & 1 != 0;
        if monitoring_enabled {
            self.environment.cycle_counter = self.environment.cycle_counter.wrapping_add(1);
        }
        bus.begin_transaction()?;
        let result = (|| {
            let pc = self.pc().map_err(SailBusExecutionError::Core)?;
            let bytes = fetch_virtual_instruction(self, bus, pc)?;
            self.execute_bus_transaction(bus, &bytes)
        })();
        let retired = matches!(result, Ok(SailBusCompletion::Retired));
        match &result {
            Ok(SailBusCompletion::Retired | SailBusCompletion::VectorLane) => {
                bus.commit_transaction();
            }
            Ok(SailBusCompletion::DebugStop) | Err(_) => bus.rollback_transaction(),
        }
        if monitoring_enabled && retired {
            self.environment.retired_instruction_counter =
                self.environment.retired_instruction_counter.wrapping_add(1);
        }
        result.map(|_| ())
    }

    /// Executes one already-framed instruction inside one bus transaction.
    ///
    pub fn execute_on_bus(
        &mut self,
        bus: &mut impl Bus,
        bytes: &[u8],
    ) -> Result<(), SailBusExecutionError> {
        bus.begin_transaction()?;
        let result = self.execute_bus_transaction(bus, bytes);
        match &result {
            Ok(SailBusCompletion::Retired | SailBusCompletion::VectorLane) => {
                bus.commit_transaction();
            }
            Ok(SailBusCompletion::DebugStop) | Err(_) => bus.rollback_transaction(),
        }
        result.map(|_| ())
    }

    fn execute_bus_transaction(
        &mut self,
        bus: &mut impl Bus,
        bytes: &[u8],
    ) -> Result<SailBusCompletion, SailBusExecutionError> {
        let mut status = self.execute(bytes);
        let mut active_request = None;
        loop {
            match status {
                SailCoreStatus::Ok => return Ok(SailBusCompletion::Retired),
                SailCoreStatus::VectorLane => return Ok(SailBusCompletion::VectorLane),
                SailCoreStatus::DebugStop => return Ok(SailBusCompletion::DebugStop),
                SailCoreStatus::NeedsEnvironment => {
                    let request = self.last_request().map_err(SailBusExecutionError::Core)?;
                    active_request = Some(Box::new(request.clone()));
                    let response = match service_request(self, bus, &request) {
                        Ok(response) => response,
                        Err(error) => {
                            let cancel = self.cancel();
                            if cancel != SailCoreStatus::Ok {
                                return Err(SailBusExecutionError::Core(cancel));
                            }
                            return Err(error);
                        }
                    };
                    status = self.resume(response);
                }
                SailCoreStatus::Fault => {
                    let fault = self.last_fault().map_err(SailBusExecutionError::Core)?;
                    if fault.address_valid != 0 {
                        if let Some(request) = active_request.as_mut() {
                            request.effective_address = fault.effective_address;
                            request.linear_address = fault.linear_address;
                            request.width = fault.width;
                        }
                    }
                    return Err(SailBusExecutionError::Fault {
                        fault,
                        request: active_request,
                    });
                }
                other => return Err(SailBusExecutionError::Core(other)),
            }
        }
    }
}

fn fetch_instruction(bus: &mut impl Bus, pc: u64) -> Result<Vec<u8>, SailBusExecutionError> {
    fetch_instruction_with(pc, |address| Ok(bus.read_u8(address)?))
}

fn fetch_virtual_instruction(
    core: &mut SailCore,
    bus: &mut impl Bus,
    pc: u64,
) -> Result<Vec<u8>, SailBusExecutionError> {
    let state = core.state().map_err(SailBusExecutionError::Core)?;
    let ptcr = state.controls.base_ptcr;
    let monitoring_enabled = state.controls.base_pmc & 1 != 0;
    let mut latest_mapping: Option<TranslationResult> = None;
    fetch_instruction_with(pc, |effective| {
        let linear = translation::segment_linear(state.segments[0], effective)
            .map_err(|fault| fetch_translation_fault(fault, effective, effective))?;
        let translated = match latest_mapping
            .and_then(|previous| previous.within_mapping(bus, linear))
        {
            Some(translated) => translated,
            None => {
                translation::translate(
                    bus, linear, ptcr, TranslationAccess::Execute, false,
                    state.supervisor != 0,
                    || core.environment.page_walk_started(monitoring_enabled),
                ).map_err(|error| match error {
                    TranslationError::Fault(fault) => fetch_translation_fault(fault, effective, linear),
                    TranslationError::Bus(error) => SailBusExecutionError::Bus(error),
                })?
            }
        };
        latest_mapping = Some(translated);
        if translated.access_class != 0 {
            return Err(fetch_translation_fault(
                translation::TranslationFault {
                    kind: translation::FAULT_ACCESS,
                    cause: 2,
                    detail: "instruction fetch from MMIO is not permitted".to_owned(),
                },
                effective,
                linear,
            ));
        }
        if translated.physical_class != 0 {
            return Err(fetch_translation_fault(
                translation::TranslationFault {
                    kind: translation::FAULT_TRANSLATION,
                    cause: 6,
                    detail: "Normal instruction mapping targets Device memory".to_owned(),
                },
                effective,
                linear,
            ));
        }
        Ok(bus.read_u8(translated.address)?)
    })
}

fn fetch_translation_fault(
    fault: translation::TranslationFault,
    effective: u64,
    linear: u64,
) -> SailBusExecutionError {
    SailBusExecutionError::Fault {
        fault: SailCoreFault {
            kind: fault.kind,
            error_code: fault.cause as u64 | (3 << 8) | (1 << 27),
            ..SailCoreFault::default()
        },
        request: Some(Box::new(SailCoreRequest {
            access: transaction_access::EXECUTE,
            domain: 2,
            segment: 0,
            width: 1,
            effective_address: effective,
            linear_address: linear,
            address_translation: true,
            ..SailCoreRequest::default()
        })),
    }
}

fn fetch_instruction_with(
    pc: u64,
    mut read: impl FnMut(u64) -> Result<u8, SailBusExecutionError>,
) -> Result<Vec<u8>, SailBusExecutionError> {
    let first = read(pc)?;
    let (length, second) = if first & 0x80 == 0 {
        (1usize, None)
    } else if first & 0xc0 == 0x80 {
        (2usize, None)
    } else {
        let second_address = pc.checked_add(1).ok_or(BusError::OutOfRange { addr: pc })?;
        let second = read(second_address)?;
        let length = usize::from(3 + ((first >> 2) & 0x0f));
        let selector = ((first & 0x03) << 4) | (second >> 4);
        let encoding_prefix = ((first & 0x03) << 6) | (second >> 2);
        let minimum = if selector <= 59 {
            3
        } else if selector <= 62 {
            4
        } else if encoding_prefix == 255 {
            6
        } else {
            5
        };
        if length < minimum {
            return Err(SailBusExecutionError::InvalidFraming {
                first,
                second: Some(second),
            });
        }
        (length, Some(second))
    };
    let mut bytes = Vec::with_capacity(length);
    bytes.push(first);
    if let Some(second) = second {
        bytes.push(second);
    }
    while bytes.len() < length {
        let address = pc
            .checked_add(bytes.len() as u64)
            .ok_or(BusError::OutOfRange { addr: pc })?;
        bytes.push(read(address)?);
    }
    Ok(bytes)
}

fn service_request(
    core: &mut SailCore,
    bus: &mut impl Bus,
    request: &SailCoreRequest,
) -> Result<SailCoreResponse, SailBusExecutionError> {
    let response_kind = match request.kind {
        request_kind::TRANSLATION_EXECUTE_PROBE => response_kind::TRANSLATION,
        request_kind::MEMORY_PROBE => response_kind::PROBE,
        request_kind::READ => response_kind::READ,
        request_kind::WRITE => response_kind::WRITE,
        request_kind::STACK_RANGE => response_kind::STACK_RANGE,
        request_kind::SEGMENT_BOUNDS_POINT => response_kind::SEGMENT_BOUNDS,
        request_kind::ATOMIC => response_kind::ATOMIC,
        request_kind::ADDRESS_WAKE => response_kind::ADDRESS_WAKE,
        request_kind::ADDRESS_TRANSLATE_REQUEST => response_kind::ADDRESS_TRANSLATION,
        request_kind::PHYSICAL_PTE_READ => response_kind::PTE_READ,
        request_kind::CACHE_MAINTENANCE_BLOCK | request_kind::PREFETCH_HINT => {
            response_kind::CACHE_MAINTENANCE
        }
        request_kind::FENCE_COMPLETION => response_kind::FENCE_COMPLETION,
        request_kind::TLB_INVALIDATE_REQUEST => response_kind::TLB_OPERATION,
        request_kind::TRANSLATION_QUERY_REQUEST => response_kind::TRANSLATION_QUERY,
        request_kind::CONTEXT_SWITCH_REQUEST => response_kind::CONTEXT_SWITCH,
        request_kind::REPEAT_BODY_FETCH => response_kind::REPEAT_FETCH,
        request_kind::EVENT_FRAME_ACCESS => response_kind::EVENT_FRAME,
        request_kind::CPUID_QUERY_REQUEST => response_kind::CPUID_QUERY,
        request_kind::PERFORMANCE_COUNTER_REQUEST => response_kind::PERFORMANCE_COUNTER,
        request_kind::CONTROL_TRANSITION_REQUEST => response_kind::CONTROL_TRANSITION,
        request_kind::RESET_SERIALIZE_REQUEST => response_kind::RESET_SERIALIZE,
        kind => return Err(SailBusExecutionError::UnsupportedRequest(kind)),
    };

    let mut response = SailCoreResponse {
        kind: response_kind,
        success: true,
        bounds_passed: true,
        known: true,
        present: true,
        ..SailCoreResponse::default()
    };

    match request.kind {
        request_kind::CPUID_QUERY_REQUEST => {
            response.value = crate::platform::cpuid_query(request.selector);
            return Ok(response);
        }
        request_kind::PERFORMANCE_COUNTER_REQUEST => {
            let value = match request.selector {
                1 => Some(core.environment.cycle_counter),
                2 => Some(core.environment.retired_instruction_counter),
                3 => Some(core.environment.page_walk_counter),
                _ => None,
            };
            response.known = value.is_some();
            response.present = value.is_some();
            response.value = value.unwrap_or_default();
            return Ok(response);
        }
        request_kind::FENCE_COMPLETION
        | request_kind::TLB_INVALIDATE_REQUEST
        | request_kind::TRANSLATION_QUERY_REQUEST
        | request_kind::CONTEXT_SWITCH_REQUEST
        | request_kind::CONTROL_TRANSITION_REQUEST
        | request_kind::RESET_SERIALIZE_REQUEST => return Ok(response),
        _ => {}
    }
    if request.kind == request_kind::STACK_RANGE
        || request.kind == request_kind::SEGMENT_BOUNDS_POINT
    {
        response.bounds_passed = stack_range_in_bounds(request);
        return Ok(response);
    }

    response.fault_range = Some(request_fault_range(request, request.linear_address));
    let data_ranges = if request.kind == request_kind::READ
        && request.access != transaction_access::ADDRESS_ONLY
    {
        let length = match usize::try_from(request.body_length) {
            Ok(length) => length,
            Err(_) => {
                set_access_failure(&mut response, invalid_request_range(request.linear_address));
                return Ok(response);
            }
        };
        Some(logical_memory_ranges(request, length, true))
    } else if request.kind == request_kind::WRITE {
        Some(logical_memory_ranges(request, request.payload.len(), false))
    } else {
        None
    };
    let data_ranges = match data_ranges {
        Some(Ok(ranges)) => Some(ranges),
        Some(Err(error)) => {
            set_access_failure(&mut response, error);
            return Ok(response);
        }
        None => None,
    };
    let validation_ranges = match data_ranges.as_ref() {
        Some(ranges) => match logical_validation_ranges(request, ranges) {
            Ok(ranges) => Some(ranges),
            Err(error) => {
                set_access_failure(&mut response, error);
                return Ok(response);
            }
        },
        None => None,
    };
    if validation_ranges.as_ref().is_some_and(Vec::is_empty) {
        let result = if request.kind == request_kind::READ {
            service_read(bus, request.linear_address, request, &mut response, &[])
        } else {
            service_write(bus, request.linear_address, request, &mut response, &[])
        };
        if let Err(error) = result {
            set_access_failure(&mut response, error);
        }
        if request.debug_validation {
            response.body.clear();
        }
        if response.success {
            response.fault_range = None;
        }
        return Ok(response);
    }
    let first_linear_address = validation_ranges
        .as_ref()
        .and_then(|ranges| ranges.iter().map(|range| range.linear_address).min())
        .unwrap_or(request.linear_address);

    let mut request_translation = None;
    response.fault_range = Some(request_fault_range(request, first_linear_address));
    let address = if request.debug_validated {
        let physical_class = match bus.physical_memory_class(request.physical_address) {
            PhysicalMemoryClass::Normal => 0,
            PhysicalMemoryClass::Device => 1,
        };
        response.access_class = physical_class;
        response.physical_class = physical_class;
        response.cache_policy = request.cache_policy;
        request.physical_address
    } else if request.address_translation {
        let ptcr = core.control(0).map_err(SailBusExecutionError::Core)?;
        let monitoring_enabled = core.control(0x1100).map_err(SailBusExecutionError::Core)? & 1 != 0;
        let access = match request.access {
            transaction_access::STORE
            | transaction_access::READ_MODIFY_WRITE
            | transaction_access::STACK_WRITE => TranslationAccess::Write,
            transaction_access::EXECUTE => TranslationAccess::Execute,
            _ => TranslationAccess::Read,
        };
        let supervisor = match request.role {
            request_role::EVENT_ENTRY_TARGET
            | request_role::EVENT_FRAME_RANGE
            | request_role::EVENT_FRAME_STORE => true,
            _ => core.is_supervisor().map_err(SailBusExecutionError::Core)?,
        };
        match translation::translate(
            bus,
            first_linear_address,
            ptcr,
            access,
            request.domain == 1,
            supervisor,
            || core.environment.page_walk_started(monitoring_enabled),
        ) {
            Ok(translated) => {
                response.access_class = translated.access_class;
                response.physical_class = translated.physical_class;
                response.cache_policy = translated.cache_policy;
                response.value = translated.address;
                request_translation = Some(RequestTranslation {
                    ptcr,
                    access,
                    user_domain: request.domain == 1,
                    supervisor,
                    monitoring_enabled,
                    first: translated,
                });
            }
            Err(TranslationError::Fault(fault)) => {
                response.success = false;
                response.fault_kind = fault.kind;
                response.fault_cause = fault.cause;
                response.detail = fault.detail;
                return Ok(response);
            }
            Err(TranslationError::Bus(error)) => return Err(error.into()),
        }
        response.value
    } else {
        match translation::translate(
            bus,
            first_linear_address,
            0,
            TranslationAccess::Read,
            false,
            true,
            || {},
        ) {
            Ok(translated) => {
                response.access_class = translated.access_class;
                response.physical_class = translated.physical_class;
            }
            Err(TranslationError::Fault(fault)) => {
                response.success = false;
                response.fault_kind = fault.kind;
                response.fault_cause = fault.cause;
                response.detail = fault.detail;
                return Ok(response);
            }
            Err(TranslationError::Bus(error)) => return Err(error.into()),
        }
        first_linear_address
    };

    let prepared_ranges = if let Some(logical) = data_ranges {
        match prepare_memory_ranges(
            core, bus, request, address, request_translation, &logical,
            validation_ranges.as_deref().unwrap_or(&[]),
        ) {
            Ok(ranges) => Some(ranges),
            Err(PrepareMemoryRangesError::Invalid(error)) => {
                set_access_failure(&mut response, error);
                return Ok(response);
            }
            Err(PrepareMemoryRangesError::Fault(fault, linear_address)) => {
                response.success = false;
                response.fault_range = Some(request_fault_range(request, linear_address));
                response.fault_kind = fault.kind;
                response.fault_cause = fault.cause;
                response.detail = fault.detail;
                return Ok(response);
            }
            Err(PrepareMemoryRangesError::Bus(error)) => return Err(error.into()),
        }
    } else {
        None
    };

    if request.debug_validation {
        response.value = address;
        response.fault_range = None;
        return Ok(response);
    }

    let operation = match request.kind {
        request_kind::TRANSLATION_EXECUTE_PROBE | request_kind::MEMORY_PROBE => Ok(()),
        request_kind::ADDRESS_TRANSLATE_REQUEST => {
            response.value = address;
            Ok(())
        }
        request_kind::PHYSICAL_PTE_READ => {
            read_width(bus, address, request.width).map(|value| response.value = value)
        }
        request_kind::READ => service_read(
            bus,
            address,
            request,
            &mut response,
            prepared_ranges.as_deref().unwrap_or(&[]),
        ),
        request_kind::WRITE => {
            let result = service_write(
                bus,
                address,
                request,
                &mut response,
                prepared_ranges.as_deref().unwrap_or(&[]),
            );
            if result.is_ok() && request.selector == 1 {
                response.atomic_store_happened = true;
            }
            result
        }
        request_kind::ATOMIC => service_atomic(bus, address, request, &mut response),
        request_kind::ADDRESS_WAKE => Ok(()),
        request_kind::CACHE_MAINTENANCE_BLOCK
        | request_kind::PREFETCH_HINT
        | request_kind::FENCE_COMPLETION
        | request_kind::TLB_INVALIDATE_REQUEST
        | request_kind::TRANSLATION_QUERY_REQUEST
        | request_kind::CONTEXT_SWITCH_REQUEST => Ok(()),
        request_kind::REPEAT_BODY_FETCH => match fetch_instruction(bus, address) {
            Ok(body) => {
                response.body = body;
                Ok(())
            }
            Err(SailBusExecutionError::Bus(error)) => Err(error),
            Err(error) => Err(BusError::Device {
                addr: address,
                message: error.to_string(),
            }),
        },
        request_kind::EVENT_FRAME_ACCESS => {
            service_blob_access(bus, address, request, &mut response)
        }
        _ => unreachable!(),
    };
    if let Err(error) = operation {
        set_access_failure(&mut response, error);
    }
    if response.success {
        response.fault_range = None;
    }
    Ok(response)
}

fn service_blob_access(
    bus: &mut impl Bus,
    address: u64,
    request: &SailCoreRequest,
    response: &mut SailCoreResponse,
) -> Result<(), BusError> {
    match request.access {
        transaction_access::LOAD
        | transaction_access::READ_MODIFY_WRITE
        | transaction_access::STACK_READ => {
            response.body = read_payload(bus, address, request.body_length)?;
            Ok(())
        }
        transaction_access::STORE | transaction_access::STACK_WRITE => {
            write_payload(bus, address, &request.payload)
        }
        transaction_access::ADDRESS_ONLY => Ok(()),
        _ => Err(BusError::InvalidRange {
            start: address,
            end: address,
        }),
    }
}

fn set_access_failure(response: &mut SailCoreResponse, error: BusError) {
    response.success = false;
    response.fault_kind = translation::FAULT_ACCESS;
    response.fault_cause = 0;
    response.detail = error.to_string();
}

fn service_read(
    bus: &mut impl Bus,
    address: u64,
    request: &SailCoreRequest,
    response: &mut SailCoreResponse,
    ranges: &[PreparedMemoryRange],
) -> Result<(), BusError> {
    if request.access == transaction_access::ADDRESS_ONLY {
        return Ok(());
    }
    if request.access != transaction_access::LOAD
        && request.access != transaction_access::READ_MODIFY_WRITE
        && request.access != transaction_access::STACK_READ
    {
        return Err(invalid_request_range(address));
    }
    let length =
        usize::try_from(request.body_length).map_err(|_| invalid_request_range(address))?;
    let mut body = vec![0; length];
    for range in ranges {
        response.fault_range = Some(range.fault_range);
        for index in 0..range.length {
            let current =
                range
                    .physical_address
                    .checked_add(index as u64)
                    .ok_or(BusError::OutOfRange {
                        addr: range.physical_address,
                    })?;
            body[range.buffer_offset + index] = bus.read_u8(current)?;
        }
    }
    response.body = body;
    Ok(())
}

fn service_write(
    bus: &mut impl Bus,
    address: u64,
    request: &SailCoreRequest,
    response: &mut SailCoreResponse,
    ranges: &[PreparedMemoryRange],
) -> Result<(), BusError> {
    if request.access != transaction_access::STORE
        && request.access != transaction_access::STACK_WRITE
    {
        return Err(invalid_request_range(address));
    }
    for range in ranges {
        response.fault_range = Some(range.fault_range);
        let end = range
            .buffer_offset
            .checked_add(range.length)
            .ok_or_else(|| invalid_request_range(address))?;
        let bytes = request
            .payload
            .get(range.buffer_offset..end)
            .ok_or_else(|| invalid_request_range(address))?;
        write_payload(bus, range.physical_address, bytes)?;
    }
    Ok(())
}

fn logical_memory_ranges(
    request: &SailCoreRequest,
    buffer_length: usize,
    clip_to_buffer: bool,
) -> Result<Vec<LogicalMemoryRange>, BusError> {
    let mut result = Vec::with_capacity(request.memory_ranges.len());
    let validated_range_end = if request.debug_validated {
        None
    } else {
        Some(linear_range_end(request)?)
    };
    for range in &request.memory_ranges {
        let buffer_offset = usize::try_from(range.buffer_offset)
            .map_err(|_| invalid_request_range(request.linear_address))?;
        let width = usize::try_from(range.width)
            .map_err(|_| invalid_request_range(request.linear_address))?;
        let length = if clip_to_buffer {
            if buffer_offset >= buffer_length {
                continue;
            }
            width.min(buffer_length - buffer_offset)
        } else {
            let end = buffer_offset
                .checked_add(width)
                .ok_or_else(|| invalid_request_range(request.linear_address))?;
            if end > buffer_length {
                return Err(invalid_request_range(request.linear_address));
            }
            width
        };
        if length == 0 {
            continue;
        }
        if range.linear_address < request.linear_address {
            return Err(invalid_request_range(request.linear_address));
        }
        if let Some(range_end) = validated_range_end {
            let linear_end = u128::from(range.linear_address) + length as u128;
            if linear_end > range_end {
                return Err(invalid_request_range(request.linear_address));
            }
        }
        result.push(LogicalMemoryRange {
            linear_address: range.linear_address,
            buffer_offset,
            length,
        });
    }
    Ok(result)
}

fn logical_validation_ranges(
    request: &SailCoreRequest,
    accesses: &[LogicalMemoryRange],
) -> Result<Vec<SailCoreValidationRange>, BusError> {
    let envelope_end = if request.debug_validated {
        None
    } else {
        Some(linear_range_end(request)?)
    };
    let mut ranges = Vec::new();
    for range in &request.validation_ranges {
        let width = u128::try_from(range.width)
            .map_err(|_| invalid_request_range(range.linear_address))?;
        if width == 0 {
            continue;
        }
        if range.linear_address < request.linear_address
            || envelope_end.is_some_and(|end| u128::from(range.linear_address) + width > end)
        {
            return Err(invalid_request_range(range.linear_address));
        }
        ranges.push(*range);
    }
    ranges.sort_by_key(|range| range.linear_address);
    for access in accesses {
        let end = u128::from(access.linear_address) + access.length as u128;
        let mut covered = u128::from(access.linear_address);
        for range in &ranges {
            if covered >= end {
                break;
            }
            if u128::from(range.linear_address) > covered {
                break;
            }
            covered = covered.max(u128::from(range.linear_address) + range.width as u128);
        }
        if covered < end {
            return Err(invalid_request_range(access.linear_address));
        }
    }
    Ok(ranges)
}

fn prepare_memory_ranges(
    core: &mut SailCore,
    bus: &mut impl Bus,
    request: &SailCoreRequest,
    address: u64,
    translation: Option<RequestTranslation>,
    ranges: &[LogicalMemoryRange],
    validation_ranges: &[SailCoreValidationRange],
) -> Result<Vec<PreparedMemoryRange>, PrepareMemoryRangesError> {
    let Some(translation) = translation else {
        let first_linear_address = validation_ranges
            .iter()
            .map(|range| range.linear_address)
            .min()
            .unwrap_or(request.linear_address);
        return ranges
            .iter()
            .map(|range| {
                let physical_address =
                    memory_range_address(address, first_linear_address, range.linear_address)?;
                physical_address
                    .checked_add((range.length - 1) as u64)
                    .ok_or(BusError::OutOfRange {
                        addr: physical_address,
                    })?;
                Ok(PreparedMemoryRange {
                    physical_address,
                    buffer_offset: range.buffer_offset,
                    length: range.length,
                    fault_range: request_fault_range(request, range.linear_address),
                })
            })
            .collect::<Result<Vec<_>, BusError>>()
            .map_err(PrepareMemoryRangesError::Invalid);
    };

    let page_mask = MIN_PAGE_BYTES - 1;
    let first_page = translation.first.linear_address & !page_mask;
    let mut latest_mapping = translation.first;
    let mut translated_pages = BTreeMap::from([(first_page, latest_mapping)]);
    let mut active_pages = BTreeMap::new();
    for range in validation_ranges {
        let end = u128::from(range.linear_address) + range.width as u128;
        let mut cursor = u128::from(range.linear_address);
        while cursor < end {
            let linear = u64::try_from(cursor).map_err(|_| {
                PrepareMemoryRangesError::Invalid(invalid_request_range(range.linear_address))
            })?;
            let page = linear & !page_mask;
            active_pages
                .entry(page)
                .and_modify(|first: &mut u64| *first = (*first).min(linear))
                .or_insert(linear);
            cursor = end.min(u128::from(page) + u128::from(MIN_PAGE_BYTES));
        }
    }
    for (page, validation_address) in active_pages {
        if let std::collections::btree_map::Entry::Vacant(entry) = translated_pages.entry(page) {
            latest_mapping = match latest_mapping.within_mapping(bus, validation_address) {
                Some(translated) => translated,
                None => translate_page(core, bus, translation, validation_address)?,
            };
            entry.insert(latest_mapping);
        }
    }
    validate_translated_pages(request, &translated_pages)?;

    let mut prepared = Vec::new();
    for range in ranges {
        let mut linear_cursor = u128::from(range.linear_address);
        let mut buffer_offset = range.buffer_offset;
        let mut remaining = range.length;
        while remaining != 0 {
            let linear_address = u64::try_from(linear_cursor).map_err(|_| {
                PrepareMemoryRangesError::Invalid(invalid_request_range(request.linear_address))
            })?;
            let page = linear_address & !page_mask;
            let page_offset = linear_address & page_mask;
            let page_remaining = (MIN_PAGE_BYTES - page_offset) as usize;
            let length = remaining.min(page_remaining);
            let translated = translated_pages
                .get(&page)
                .ok_or_else(|| {
                    PrepareMemoryRangesError::Invalid(invalid_request_range(request.linear_address))
                })?;
            let physical_page = translated.address
                .checked_sub(translated.linear_address & page_mask)
                .ok_or_else(|| PrepareMemoryRangesError::Invalid(invalid_request_range(linear_address)))?;
            let physical_address = physical_page.checked_add(page_offset).ok_or_else(|| {
                PrepareMemoryRangesError::Invalid(BusError::OutOfRange {
                    addr: physical_page,
                })
            })?;
            physical_address
                .checked_add((length - 1) as u64)
                .ok_or_else(|| {
                    PrepareMemoryRangesError::Invalid(BusError::OutOfRange {
                        addr: physical_address,
                    })
                })?;
            prepared.push(PreparedMemoryRange {
                physical_address,
                buffer_offset,
                length,
                fault_range: request_fault_range(request, range.linear_address),
            });
            linear_cursor += length as u128;
            buffer_offset = buffer_offset
                .checked_add(length)
                .ok_or_else(|| PrepareMemoryRangesError::Invalid(invalid_request_range(address)))?;
            remaining -= length;
        }
    }
    Ok(prepared)
}

fn translate_page(
    core: &mut SailCore,
    bus: &mut impl Bus,
    translation: RequestTranslation,
    linear_address: u64,
) -> Result<TranslationResult, PrepareMemoryRangesError> {
    let translated = match translation::translate(
        bus,
        linear_address,
        translation.ptcr,
        translation.access,
        translation.user_domain,
        translation.supervisor,
        || core.environment.page_walk_started(translation.monitoring_enabled),
    ) {
        Ok(translated) => translated,
        Err(TranslationError::Fault(fault)) => {
            return Err(PrepareMemoryRangesError::Fault(fault, linear_address));
        }
        Err(TranslationError::Bus(error)) => {
            return Err(PrepareMemoryRangesError::Bus(error));
        }
    };
    Ok(translated)
}

fn request_fault_range(request: &SailCoreRequest, linear: u64) -> SailCoreValidationRange {
    request.validation_ranges.iter().copied().find(|range| {
        range.width > 0
            && range.linear_address <= linear
            && u128::from(linear) < u128::from(range.linear_address) + range.width as u128
    }).unwrap_or(SailCoreValidationRange {
        effective_address: request.effective_address,
        linear_address: request.linear_address,
        width: request.width,
    })
}

fn validation_address_on_page(
    request: &SailCoreRequest, page: u64,
) -> Result<u64, PrepareMemoryRangesError> {
    request.validation_ranges.iter().filter(|range| {
        range.width > 0
            && u128::from(range.linear_address) < u128::from(page) + u128::from(MIN_PAGE_BYTES)
            && u128::from(page) < u128::from(range.linear_address) + range.width as u128
    }).map(|range| range.linear_address.max(page)).min()
        .ok_or_else(|| PrepareMemoryRangesError::Invalid(invalid_request_range(page)))
}

fn validate_translated_pages(
    request: &SailCoreRequest,
    translated_pages: &BTreeMap<u64, TranslationResult>,
) -> Result<(), PrepareMemoryRangesError> {
    let mut first_mmio = None;
    for (page, translated) in translated_pages {
        if translated.access_class == 0 && translated.physical_class != 0 {
            return Err(PrepareMemoryRangesError::Fault(TranslationFault {
                kind: translation::FAULT_TRANSLATION,
                cause: 6,
                detail: "Normal access class selected Device physical memory".to_owned(),
            }, validation_address_on_page(request, *page)?));
        }
        if translated.access_class != 0 && first_mmio.is_none() {
            first_mmio = Some(validation_address_on_page(request, *page)?);
        }
    }
    if let Some(mmio_address) = first_mmio.filter(|_| !request.suppress_fault) {
        let scalar = matches!(request.kind, request_kind::READ | request_kind::WRITE)
            && request.range_length == request.width
            && request.selector == 0
            && matches!(request.width, 1 | 2 | 4 | 8);
        if !scalar {
            return Err(PrepareMemoryRangesError::Fault(TranslationFault {
                kind: translation::FAULT_ACCESS,
                cause: 2,
                detail: "operation is not permitted for MMIO".to_owned(),
            }, mmio_address));
        }
        if request.linear_address % request.width as u64 != 0 {
            return Err(PrepareMemoryRangesError::Fault(TranslationFault {
                kind: translation::FAULT_ACCESS,
                cause: 1,
                detail: "MMIO scalar is not naturally aligned".to_owned(),
            }, request.linear_address));
        }
    }
    Ok(())
}

fn linear_range_end(request: &SailCoreRequest) -> Result<u128, BusError> {
    let range_length = u128::try_from(request.range_length)
        .map_err(|_| invalid_request_range(request.linear_address))?;
    let end = u128::from(request.linear_address) + range_length;
    if end > (1u128 << u64::BITS) {
        return Err(invalid_request_range(request.linear_address));
    }
    Ok(end)
}

fn memory_range_address(
    address: u64,
    request_linear_address: u64,
    range_linear_address: u64,
) -> Result<u64, BusError> {
    let offset = range_linear_address
        .checked_sub(request_linear_address)
        .ok_or_else(|| invalid_request_range(address))?;
    address
        .checked_add(offset)
        .ok_or(BusError::OutOfRange { addr: address })
}

fn invalid_request_range(address: u64) -> BusError {
    BusError::InvalidRange {
        start: address,
        end: address,
    }
}

fn read_payload(bus: &mut impl Bus, address: u64, length: i64) -> Result<Vec<u8>, BusError> {
    let length = usize::try_from(length).map_err(|_| BusError::InvalidRange {
        start: address,
        end: address,
    })?;
    let mut bytes = Vec::with_capacity(length);
    for offset in 0..length {
        let current = address
            .checked_add(offset as u64)
            .ok_or(BusError::OutOfRange { addr: address })?;
        bytes.push(bus.read_u8(current)?);
    }
    Ok(bytes)
}

fn write_payload(bus: &mut impl Bus, address: u64, bytes: &[u8]) -> Result<(), BusError> {
    for (offset, byte) in bytes.iter().copied().enumerate() {
        let current = address
            .checked_add(offset as u64)
            .ok_or(BusError::OutOfRange { addr: address })?;
        bus.write_u8(current, byte)?;
    }
    Ok(())
}

fn stack_range_in_bounds(request: &SailCoreRequest) -> bool {
    if request.range_length < 0 {
        return false;
    }
    let image = request.segment_image;
    let mantissa = (image >> 1) & 0x3f;
    if mantissa == 0 {
        return u128::from(request.range_start) + request.range_length as u128
            <= u128::from(u64::MAX) + 1;
    }
    let exponent = (image >> 7) & 0x1f;
    let base = u128::from((image >> 12) << 12);
    let span = (u128::from(mantissa) << exponent) * 4096;
    let start = u128::from(request.range_start);
    let end = start + request.range_length as u128;
    if image & 1 != 0 {
        base <= start && end <= base + span
    } else {
        end <= span
    }
}

fn read_width(bus: &mut impl Bus, address: u64, width: i64) -> Result<u64, BusError> {
    match width {
        1 => bus.read_u8(address).map(u64::from),
        2 => bus.read_u16(address).map(u64::from),
        4 => bus.read_u32(address).map(u64::from),
        8 => bus.read_u64(address),
        _ => Err(BusError::InvalidRange {
            start: address,
            end: address,
        }),
    }
}

fn service_atomic(
    bus: &mut impl Bus,
    address: u64,
    request: &SailCoreRequest,
    response: &mut SailCoreResponse,
) -> Result<(), BusError> {
    if request.width != 8 {
        return Err(BusError::InvalidRange {
            start: address,
            end: address,
        });
    }
    if request.selector == 0 {
        match bus.compare_exchange_u64(address, request.expected, request.desired)? {
            Ok(observed) => {
                response.value = observed;
                response.atomic_store_happened = true;
            }
            Err(observed) => response.value = observed,
        }
        return Ok(());
    }
    let observed = bus.read_u64(address)?;
    let desired = match request.selector {
        1 => observed.wrapping_add(request.value),
        2 => observed & request.value,
        3 => observed | request.value,
        4 => observed.wrapping_sub(request.value),
        5 => observed ^ request.value,
        _ => {
            return Err(BusError::Device {
                addr: address,
                message: format!("unknown atomic selector {}", request.selector),
            });
        }
    };
    bus.write_u64(address, desired)?;
    response.value = observed;
    response.atomic_store_happened = true;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{request_kind, service_request, transaction_access};
    use crate::{SailCore, SailCoreMemoryRange, SailCoreRequest, SailCoreStatus};
    use bedrock_bus::{Bus, BusResult, PhysicalMemoryClass, Ram};

    fn write_control(core: &mut SailCore, selector: u16, value: u64) {
        assert_eq!(core.set_register(0, value), SailCoreStatus::Ok);
        let [low, high] = selector.to_le_bytes();
        assert_eq!(
            core.execute(&[0xcb, 0xb5, 0x10, low, high]),
            SailCoreStatus::Ok,
            "WRCR {selector:#06x} with {value:#018x}"
        );
    }

    struct RejectTargetAccessBus;

    impl Bus for RejectTargetAccessBus {
        fn begin_transaction(&mut self) -> BusResult<()> {
            Ok(())
        }

        fn commit_transaction(&mut self) {}

        fn rollback_transaction(&mut self) {}

        fn read_u8(&mut self, addr: u64) -> BusResult<u8> {
            panic!("validation-only request read target address {addr:#x}")
        }

        fn write_u8(&mut self, addr: u64, _value: u8) -> BusResult<()> {
            panic!("validation-only request wrote target address {addr:#x}")
        }
    }

    struct DeviceTailBus {
        ram: Ram,
        target_accesses: usize,
    }

    impl Bus for DeviceTailBus {
        fn begin_transaction(&mut self) -> BusResult<()> {
            Bus::begin_transaction(&mut self.ram)
        }

        fn commit_transaction(&mut self) {
            Bus::commit_transaction(&mut self.ram);
        }

        fn rollback_transaction(&mut self) {
            Bus::rollback_transaction(&mut self.ram);
        }

        fn read_u8(&mut self, addr: u64) -> BusResult<u8> {
            if addr >= 0x1_0000 {
                self.target_accesses += 1;
            }
            self.ram.read_u8(addr)
        }

        fn write_u8(&mut self, addr: u64, value: u8) -> BusResult<()> {
            if addr >= 0x1_0000 {
                self.target_accesses += 1;
            }
            self.ram.write_u8(addr, value)
        }

        fn physical_memory_class(&self, addr: u64) -> PhysicalMemoryClass {
            if addr >= 0x1_8000 {
                PhysicalMemoryClass::Device
            } else {
                PhysicalMemoryClass::Normal
            }
        }
    }

    #[test]
    fn debug_validation_completes_without_target_access() {
        let mut core = SailCore::new().unwrap();
        let mut bus = RejectTargetAccessBus;

        for (kind, access) in [
            (request_kind::READ, transaction_access::LOAD),
            (request_kind::WRITE, transaction_access::STORE),
            (request_kind::ATOMIC, transaction_access::READ_MODIFY_WRITE),
        ] {
            let request = SailCoreRequest {
                kind,
                access,
                width: 8,
                linear_address: 0x40,
                address_translation: true,
                debug_validation: true,
                range_length: 8,
                validation_ranges: vec![crate::SailCoreValidationRange {
                    effective_address: 0x40,
                    linear_address: 0x40,
                    width: 8,
                }],
                ..SailCoreRequest::default()
            };

            let response = service_request(&mut core, &mut bus, &request).unwrap();
            assert!(response.success);
            assert_eq!(response.value, 0x40);
        }
    }

    #[test]
    fn debug_validation_reports_a_later_translation_fault() {
        const TABLE: u64 = 0x1f;
        const LEAF_RW: u64 = 0x0d;
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.status |= 1 << 4;
        state.supervisor = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        ram.write_u64(0xc000, 0x1_0000 | LEAF_RW).unwrap();
        let request = SailCoreRequest {
            kind: request_kind::READ,
            access: transaction_access::LOAD,
            width: 16,
            range_length: 16,
            effective_address: 0x3ff8,
            linear_address: 0x3ff8,
            address_translation: true,
            debug_validation: true,
            body_length: 8,
            memory_ranges: vec![SailCoreMemoryRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 8,
                buffer_offset: 0,
            }],
            validation_ranges: vec![crate::SailCoreValidationRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
            }],
            ..SailCoreRequest::default()
        };

        let response = service_request(&mut core, &mut ram, &request).unwrap();

        assert!(!response.success);
        assert_eq!(response.fault_kind, crate::translation::FAULT_TRANSLATION);
    }

    #[test]
    fn debug_validated_access_uses_the_pretranslated_address() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(0x100);
        ram.write_u64(0x20, 0x8877_6655_4433_2211).unwrap();
        let request = SailCoreRequest {
            kind: request_kind::READ,
            access: transaction_access::LOAD,
            width: 8,
            linear_address: u64::MAX,
            debug_validated: true,
            physical_address: 0x20,
            body_length: 8,
            memory_ranges: vec![SailCoreMemoryRange {
                effective_address: 0,
                linear_address: u64::MAX,
                width: 8,
                buffer_offset: 0,
            }],
            range_length: 8,
            validation_ranges: vec![crate::SailCoreValidationRange {
                effective_address: 0,
                linear_address: u64::MAX,
                width: 8,
            }],
            ..SailCoreRequest::default()
        };

        let response = service_request(&mut core, &mut ram, &request).unwrap();

        assert!(response.success);
        assert_eq!(response.body, 0x8877_6655_4433_2211u64.to_le_bytes());
    }

    #[test]
    fn watchpoint_stopped_store_leaves_dirty_bit_clear() {
        const TABLE: u64 = 0x1f;
        const LEAF_RW: u64 = 0x0d;
        const DIRTY: u64 = 1 << 8;
        const DTRSEL: u16 = 0x0400;
        const DTRDATA: u16 = 0x0401;
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.status |= 1 << 4;
        state.supervisor = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        write_control(&mut core, DTRSEL, 1 << 16);
        write_control(&mut core, DTRDATA, 0x8000);
        write_control(&mut core, DTRSEL, 2 << 16);
        write_control(&mut core, DTRDATA, 0x8008);
        write_control(&mut core, DTRSEL, 0);
        write_control(&mut core, DTRDATA, 0x29);

        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        ram.write_u64(0xc010, 0x1_0000 | LEAF_RW).unwrap();
        ram.write_u64(0x1_0000, 0xaabb_ccdd_eeff_0011).unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.pc = 0;
        state.sp = 0x8008;
        state.registers[0] = 0x1122_3344_5566_7788;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        core.execute_on_bus(&mut ram, &[0x30]).unwrap();

        assert_eq!(ram.read_u64(0x1_0000).unwrap(), 0xaabb_ccdd_eeff_0011);
        assert_eq!(ram.read_u64(0xc010).unwrap() & DIRTY, 0);
    }

    #[test]
    fn push_round_trips_through_ram_bus() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(0x10_0000);
        assert_eq!(core.set_sp(0x10_0000), SailCoreStatus::Ok);
        assert_eq!(
            core.set_register(0, 0x1122_3344_5566_7788),
            SailCoreStatus::Ok
        );

        core.execute_on_bus(&mut ram, &[0x30]).unwrap();

        assert_eq!(core.pc(), Ok(1));
        assert_eq!(core.sp(), Ok(0x0f_fff8));
        assert_eq!(ram.read_u64(0x0f_fff8).unwrap(), 0x1122_3344_5566_7788);
    }

    #[test]
    fn push_uses_host_page_walk_when_paging_is_enabled() {
        const TABLE: u64 = 0x1f;
        const LEAF_RW: u64 = 0x0d;
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        ram.write_u64(0xc010, 0x1_0000 | LEAF_RW).unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.controls.base_pmc = 1;
        state.status |= 1 << 4;
        state.supervisor = 1;
        state.sp = 0x8008;
        state.registers[0] = 0x1122_3344_5566_7788;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        core.execute_on_bus(&mut ram, &[0x30]).unwrap();

        assert_eq!(core.sp(), Ok(0x8000));
        assert_eq!(ram.read_u64(0x1_0000).unwrap(), 0x1122_3344_5566_7788);
        assert_eq!(core.environment_state().page_walk_counter, 1);
    }

    #[test]
    fn translated_read_follows_each_minimum_page_mapping() {
        const TABLE: u64 = 0x1f;
        const LEAF_RW: u64 = 0x0d;
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.status |= 1 << 4;
        state.supervisor = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        ram.write_u64(0xc000, 0x1_0000 | LEAF_RW).unwrap();
        ram.write_u64(0xc008, 0x1_8000 | LEAF_RW).unwrap();
        ram.load(0x1_3ff8, &[0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17])
            .unwrap();
        ram.load(0x1_8000, &[0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27])
            .unwrap();
        let request = SailCoreRequest {
            kind: request_kind::READ,
            access: transaction_access::LOAD,
            width: 16,
            range_length: 16,
            effective_address: 0x3ff8,
            linear_address: 0x3ff8,
            address_translation: true,
            body_length: 16,
            memory_ranges: vec![SailCoreMemoryRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
                buffer_offset: 0,
            }],
            validation_ranges: vec![crate::SailCoreValidationRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
            }],
            ..SailCoreRequest::default()
        };

        let response = service_request(&mut core, &mut ram, &request).unwrap();

        assert!(response.success);
        assert_eq!(
            response.body,
            [
                0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25,
                0x26, 0x27,
            ]
        );
    }

    #[test]
    fn translated_write_validates_every_page_before_payload_access() {
        const TABLE: u64 = 0x1f;
        const LEAF_RW: u64 = 0x0d;
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.status |= 1 << 4;
        state.supervisor = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        ram.write_u64(0xc000, 0x1_0000 | LEAF_RW).unwrap();
        ram.write_u64(0x1_3ff8, 0x1122_3344_5566_7788).unwrap();
        let request = SailCoreRequest {
            kind: request_kind::WRITE,
            access: transaction_access::STORE,
            width: 16,
            range_length: 16,
            effective_address: 0x3ff8,
            linear_address: 0x3ff8,
            address_translation: true,
            payload: vec![0xa5; 16],
            memory_ranges: vec![SailCoreMemoryRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
                buffer_offset: 0,
            }],
            validation_ranges: vec![crate::SailCoreValidationRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
            }],
            ..SailCoreRequest::default()
        };

        let response = service_request(&mut core, &mut ram, &request).unwrap();

        assert!(!response.success);
        assert_eq!(response.fault_kind, crate::translation::FAULT_TRANSLATION);
        assert_eq!(ram.read_u64(0x1_3ff8).unwrap(), 0x1122_3344_5566_7788);
    }

    #[test]
    fn translated_record_request_validates_unselected_pages() {
        const TABLE: u64 = 0x1f;
        const LEAF_RW: u64 = 0x0d;
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.status |= 1 << 4;
        state.supervisor = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        let mut ram = Ram::new(0x20_000);
        ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        ram.write_u64(0xc000, 0x1_0000 | LEAF_RW).unwrap();
        ram.write_u64(0x1_3ff8, 0x1122_3344_5566_7788).unwrap();
        let request = SailCoreRequest {
            kind: request_kind::READ,
            access: transaction_access::LOAD,
            width: 16,
            range_length: 16,
            effective_address: 0x3ff8,
            linear_address: 0x3ff8,
            address_translation: true,
            body_length: 8,
            memory_ranges: vec![SailCoreMemoryRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 8,
                buffer_offset: 0,
            }],
            validation_ranges: vec![crate::SailCoreValidationRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
            }],
            ..SailCoreRequest::default()
        };

        let response = service_request(&mut core, &mut ram, &request).unwrap();

        assert!(!response.success);
        assert_eq!(response.fault_kind, crate::translation::FAULT_TRANSLATION);
    }

    #[test]
    fn translated_range_rejects_a_device_page_before_target_access() {
        const TABLE: u64 = 0x1f;
        const LEAF_RW: u64 = 0x0d;
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.status |= 1 << 4;
        state.supervisor = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        let mut bus = DeviceTailBus {
            ram: Ram::new(0x20_000),
            target_accesses: 0,
        };
        bus.ram.write_u64(0x4000, 0x8000 | TABLE).unwrap();
        bus.ram.write_u64(0x8000, 0xc000 | TABLE).unwrap();
        bus.ram.write_u64(0xc000, 0x1_0000 | LEAF_RW).unwrap();
        bus.ram.write_u64(0xc008, 0x1_8000 | LEAF_RW).unwrap();
        let request = SailCoreRequest {
            kind: request_kind::READ,
            access: transaction_access::LOAD,
            width: 16,
            range_length: 16,
            effective_address: 0x3ff8,
            linear_address: 0x3ff8,
            address_translation: true,
            body_length: 16,
            memory_ranges: vec![SailCoreMemoryRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
                buffer_offset: 0,
            }],
            validation_ranges: vec![crate::SailCoreValidationRange {
                effective_address: 0x3ff8,
                linear_address: 0x3ff8,
                width: 16,
            }],
            ..SailCoreRequest::default()
        };

        let response = service_request(&mut core, &mut bus, &request).unwrap();

        assert!(!response.success);
        assert_eq!(response.fault_kind, crate::translation::FAULT_TRANSLATION);
        assert_eq!(response.fault_cause, 6);
        assert_eq!(bus.target_accesses, 0);
    }

    #[test]
    fn range_ending_at_the_address_space_modulus_is_valid() {
        let mut core = SailCore::new().unwrap();
        let mut bus = RejectTargetAccessBus;
        let request = SailCoreRequest {
            kind: request_kind::READ,
            access: transaction_access::LOAD,
            width: 8,
            range_length: 8,
            range_end_at_modulus: true,
            effective_address: u64::MAX - 7,
            linear_address: u64::MAX - 7,
            address_translation: true,
            body_length: 8,
            memory_ranges: vec![SailCoreMemoryRange {
                effective_address: u64::MAX - 7,
                linear_address: u64::MAX - 7,
                width: 8,
                buffer_offset: 0,
            }],
            validation_ranges: vec![crate::SailCoreValidationRange {
                effective_address: u64::MAX - 7,
                linear_address: u64::MAX - 7,
                width: 8,
            }],
            ..SailCoreRequest::default()
        };
        let logical = super::logical_memory_ranges(&request, 8, true).unwrap();
        let prepared = super::prepare_memory_ranges(
            &mut core,
            &mut bus,
            &request,
            0x3ff8,
            Some(super::RequestTranslation {
                ptcr: 0,
                access: crate::translation::TranslationAccess::Read,
                user_domain: false,
                supervisor: true,
                monitoring_enabled: false,
                first: crate::translation::TranslationResult {
                    linear_address: u64::MAX - 7,
                    address: 0x3ff8,
                    leaf_mask: Some(crate::translation::MIN_PAGE_BYTES - 1),
                    access_class: 0,
                    physical_class: 0,
                    cache_policy: 0,
                },
            }),
            &logical,
            &request.validation_ranges,
        )
        .unwrap();

        assert_eq!(
            prepared,
            vec![super::PreparedMemoryRange {
                physical_address: 0x3ff8,
                buffer_offset: 0,
                length: 8,
                fault_range: request.validation_ranges[0],
            }]
        );
    }

    #[test]
    fn paging_fault_cause_round_trips_through_sail() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(0x10_000);
        let mut state = core.state().unwrap();
        state.controls.base_ptcr = 0x4005;
        state.status |= 1 << 4;
        state.supervisor = 1;
        state.sp = 0x0000_2000_0000_0008;
        state.registers[0] = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        let error = core.execute_on_bus(&mut ram, &[0x30]).unwrap_err();

        let super::SailBusExecutionError::Fault { fault, .. } = error else {
            panic!("expected architectural paging fault");
        };
        assert_eq!(fault.kind, crate::translation::FAULT_TRANSLATION);
        assert_eq!(fault.error_code & 0xff, 3);
    }

    #[test]
    fn failed_bus_execution_rolls_back_and_leaves_core_reusable() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        assert_eq!(core.set_sp(4), SailCoreStatus::Ok);
        assert!(matches!(
            core.execute_on_bus(&mut ram, &[0x30]),
            Err(super::SailBusExecutionError::Fault { .. })
        ));
        assert_eq!(core.pc(), Ok(0));
        assert_eq!(core.sp(), Ok(4));

        core.execute_on_bus(&mut ram, &[0x01]).unwrap();
        assert_eq!(core.pc(), Ok(1));
    }

    #[test]
    fn step_fetches_framed_instruction_from_ram() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        ram.load(0, &[0x01, 0x01]).unwrap();

        core.step_on_bus(&mut ram).unwrap();
        core.step_on_bus(&mut ram).unwrap();

        assert_eq!(core.pc(), Ok(2));
    }

    #[test]
    fn cpuid_request_round_trips_through_the_host_platform() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        assert_eq!(core.set_register(0, 0), SailCoreStatus::Ok);

        core.execute_on_bus(&mut ram, &[0xc3, 0xb4, 0x00]).unwrap();

        assert_eq!(core.register(0), Ok(0x0000_0002_0002_0012));
        assert_eq!(core.pc(), Ok(3));
    }

    #[test]
    fn scalar_fp_executes_through_the_linked_numeric_primitive() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        let mut state = core.state().unwrap();
        state.floating_registers[0] = u64::from(1.0_f32.to_bits());
        state.floating_registers[1] = u64::from(2.0_f32.to_bits());
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // medium.fadd_x_fn_s_fn_d, S, F1 -> F0
        core.execute_on_bus(&mut ram, &[0xc2, 0x82, 0x10]).unwrap();

        assert_eq!(core.pc(), Ok(3));
        assert_eq!(
            core.state().unwrap().floating_registers[0],
            u64::from(3.0_f32.to_bits())
        );
        assert_eq!(core.state().unwrap().fflags, 0);
    }

    #[test]
    fn vector_fp_executes_lane_wise_inside_the_sail_model() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        let mut state = core.state().unwrap();
        state.predicate_registers[0] = [0xff, 0xff];
        for lane in 0..4 {
            state.vector_registers[0][lane * 4..lane * 4 + 4]
                .copy_from_slice(&1.0_f32.to_bits().to_le_bytes());
            state.vector_registers[1][lane * 4..lane * 4 + 4]
                .copy_from_slice(&2.0_f32.to_bits().to_le_bytes());
        }
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // VFADD.S P0, V1, V0
        core.execute_on_bus(&mut ram, &[0xcb, 0xf7, 0x83, 0x00, 0x20])
            .unwrap();

        let state = core.state().unwrap();
        for lane in 0..4 {
            assert_eq!(
                &state.vector_registers[0][lane * 4..lane * 4 + 4],
                &3.0_f32.to_bits().to_le_bytes()
            );
        }
        assert_eq!(state.fflags, 0);
    }

    #[test]
    fn vector_half_arithmetic_classification_and_compare_round_trip() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        let mut state = core.state().unwrap();
        state.predicate_registers[0] = [0xff, 0xff];
        for lane in 0..8 {
            state.vector_registers[0][lane * 2..lane * 2 + 2]
                .copy_from_slice(&0x3c00_u16.to_le_bytes());
            state.vector_registers[1][lane * 2..lane * 2 + 2]
                .copy_from_slice(&0x4000_u16.to_le_bytes());
        }
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // VFADD.H P0, V1, V0
        core.execute_on_bus(&mut ram, &[0xcb, 0xf7, 0x82, 0x80, 0x20])
            .unwrap();
        let state = core.state().unwrap();
        for lane in 0..8 {
            assert_eq!(
                &state.vector_registers[0][lane * 2..lane * 2 + 2],
                &0x4200_u16.to_le_bytes()
            );
        }

        // VFCLASS.H P0, V0: positive normal -> class bit 6.
        core.execute_on_bus(&mut ram, &[0xc7, 0xe8, 0x84, 0x00])
            .unwrap();
        let state = core.state().unwrap();
        for lane in 0..8 {
            assert_eq!(
                &state.vector_registers[0][lane * 2..lane * 2 + 2],
                &0x0040_u16.to_le_bytes()
            );
        }

        let mut state = state;
        state.vector_registers[0] = state.vector_registers[1];
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        // VFCMPEQ.H P0, V1, V0, P1
        core.execute_on_bus(&mut ram, &[0xcb, 0xf5, 0x48, 0x04, 0x20])
            .unwrap();
        assert_eq!(core.state().unwrap().predicate_registers[1], [0x55, 0x55]);
    }

    #[test]
    fn vector_conversions_use_container_layout() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        let mut state = core.state().unwrap();
        state.predicate_registers[0] = [0x11, 0x11];
        for base in (0..16).step_by(4) {
            state.vector_registers[1][base..base + 2].copy_from_slice(&0x3c00_u16.to_le_bytes());
        }
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // VFCVTS.H P0, V1, V0
        core.execute_on_bus(&mut ram, &[0xcb, 0xf7, 0xa2, 0x80, 0x20])
            .unwrap();
        let state = core.state().unwrap();
        for base in (0..16).step_by(4) {
            assert_eq!(
                &state.vector_registers[0][base..base + 4],
                &1.0_f32.to_bits().to_le_bytes()
            );
        }
    }

    #[test]
    fn vector_fp_reduction_executes_inside_the_sail_model() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        let mut state = core.state().unwrap();
        state.predicate_registers[0] = [0x55, 0x55];
        for lane in 0..8 {
            state.vector_registers[1][lane * 2..lane * 2 + 2]
                .copy_from_slice(&0x3c00_u16.to_le_bytes());
        }
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        // VFREDADD.H P0, V1, F0
        core.execute_on_bus(&mut ram, &[0xcb, 0xf7, 0xec, 0x80, 0x01])
            .unwrap();
        assert_eq!(core.state().unwrap().floating_registers[0], 0x4800);
    }

    #[test]
    fn fptransa_request_round_trips_with_accuracy_evidence() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        let mut state = core.state().unwrap();
        state.floating_registers[1] = 0;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // FSINA.S F1, F0
        core.execute_on_bus(&mut ram, &[0xc7, 0xe7, 0x29, 0x10])
            .unwrap();
        assert_eq!(core.state().unwrap().floating_registers[0], 0);
    }

    #[test]
    fn performance_counter_request_observes_retired_steps() {
        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(16);
        let mut state = core.state().unwrap();
        state.controls.base_pmc = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        ram.load(0, &[0x01, 0xcb, 0xb4, 0x51, 0x01, 0x00]).unwrap();

        core.step_on_bus(&mut ram).unwrap();
        core.step_on_bus(&mut ram).unwrap();

        assert_eq!(core.register(1), Ok(2));
        assert_eq!(core.environment.retired_instruction_counter, 2);
    }

    #[test]
    fn resumable_scatter_stores_one_lane_per_step_and_retires_once() {
        const VSCATTER_B_SCALAR_STRIDE: [u8; 6] = [0xcf, 0xfc, 0x18, 0x02, 0x02, 0x43];

        let mut core = SailCore::new().unwrap();
        let mut ram = Ram::new(0x200);
        ram.load(0, &VSCATTER_B_SCALAR_STRIDE).unwrap();
        ram.load(0x100, &[0xa0, 0xa1, 0xa2, 0xa3, 0xa4]).unwrap();
        let mut state = core.state().unwrap();
        state.controls.base_pmc = 1;
        state.registers[1] = 0x100;
        state.registers[2] = 1;
        state.predicate_registers[0] = [0x05, 0x00];
        state.predicate_registers[1] = [0xf2, 0xff];
        state.vector_registers[3][0] = 0x11;
        state.vector_registers[3][2] = 0x33;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        core.step_on_bus(&mut ram).unwrap();

        assert_eq!(
            &ram.as_slice()[0x100..0x105],
            &[0x11, 0xa1, 0xa2, 0xa3, 0xa4]
        );
        assert_eq!(core.pc(), Ok(0));
        assert_eq!(core.state().unwrap().predicate_registers[1], [0x01, 0x00]);
        assert_eq!(core.environment.retired_instruction_counter, 0);

        core.step_on_bus(&mut ram).unwrap();

        assert_eq!(
            &ram.as_slice()[0x100..0x105],
            &[0x11, 0xa1, 0x33, 0xa3, 0xa4]
        );
        assert_eq!(core.pc(), Ok(VSCATTER_B_SCALAR_STRIDE.len() as u64));
        assert_eq!(core.state().unwrap().predicate_registers[1], [0x05, 0x00]);
        assert_eq!(core.environment.retired_instruction_counter, 1);
    }
}
