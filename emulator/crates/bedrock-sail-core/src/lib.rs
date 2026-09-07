use std::ffi::{CString, c_char, c_void};
use std::fmt;
use std::sync::{Mutex, MutexGuard};

mod bus;
mod numeric;
mod platform;
mod translation;
pub use bus::SailBusExecutionError;

pub mod protocol {
    include!(concat!(env!("OUT_DIR"), "/protocol_constants.rs"));
}

pub mod fault_kind {
    include!(concat!(env!("OUT_DIR"), "/fault_kinds.rs"));
}

pub const MAX_INSTRUCTION_BYTES: usize = 18;
pub const REGISTER_COUNT: usize = 16;
pub const FLOATING_REGISTER_COUNT: usize = 16;
pub const SEGMENT_COUNT: usize = 9;
pub const VECTOR_REGISTER_COUNT: usize = 32;
pub const PREDICATE_REGISTER_COUNT: usize = 16;
pub const MAX_VECTOR_LENGTH_BYTES: usize = 16;
pub const MAX_PREDICATE_LENGTH_BYTES: usize = 2;

static SAIL_RUNTIME: Mutex<()> = Mutex::new(());

fn runtime_lock() -> MutexGuard<'static, ()> {
    SAIL_RUNTIME
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum SailCoreStatus {
    Ok = 0,
    InvalidInstruction = 1,
    NeedsEnvironment = 2,
    Fault = 3,
    BadArgument = 4,
    OutOfMemory = 5,
    BadState = 6,
    VectorLane = 7,
    DebugStop = 8,
}

impl SailCoreStatus {
    fn from_raw(raw: i32) -> Self {
        match raw {
            0 => Self::Ok,
            1 => Self::InvalidInstruction,
            2 => Self::NeedsEnvironment,
            3 => Self::Fault,
            4 => Self::BadArgument,
            5 => Self::OutOfMemory,
            6 => Self::BadState,
            7 => Self::VectorLane,
            8 => Self::DebugStop,
            _ => panic!("emulator-core returned unknown status {raw}"),
        }
    }
}

#[derive(Debug)]
pub enum SailCoreCreateError {
    Status(SailCoreStatus),
}

impl fmt::Display for SailCoreCreateError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Status(status) => write!(formatter, "failed to create Sail core: {status:?}"),
        }
    }
}

impl std::error::Error for SailCoreCreateError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
#[repr(C)]
pub struct SailCoreFault {
    pub kind: i32,
    pub operation: i32,
    pub error_code: u64,
    pub bus_error: u8,
    pub address_valid: u8,
    pub effective_address: u64,
    pub linear_address: u64,
    pub width: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
#[repr(C)]
pub struct SailControlState {
    pub base_ptcr: u64,
    pub base_ascr: u64,
    pub base_ecr: u64,
    pub base_upc: u64,
    pub base_usp: u64,
    pub base_ucs: u64,
    pub base_uds: u64,
    pub base_uss: u64,
    pub base_uctl: u64,
    pub base_uinfo: u64,
    pub base_epc: u64,
    pub base_ecs: u64,
    pub base_eds: u64,
    pub base_sss: u64,
    pub base_ssp: u64,
    pub base_iss: u64,
    pub base_isp: u64,
    pub base_fss: u64,
    pub base_fsp: u64,
    pub base_dss: u64,
    pub base_dsp: u64,
    pub base_bootpc: u64,
    pub base_bootcfg: u64,
    pub base_pmc: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
#[repr(C)]
pub struct SailCoreState {
    pub registers: [u64; REGISTER_COUNT],
    pub floating_registers: [u64; FLOATING_REGISTER_COUNT],
    pub vector_registers: [[u8; MAX_VECTOR_LENGTH_BYTES]; VECTOR_REGISTER_COUNT],
    pub predicate_registers: [[u8; MAX_PREDICATE_LENGTH_BYTES]; PREDICATE_REGISTER_COUNT],
    pub sp: u64,
    pub pc: u64,
    pub lpc: u64,
    pub lpa: u64,
    pub lkl: u64,
    pub lkh: u64,
    pub flags: u64,
    pub status: u64,
    pub segments: [u64; SEGMENT_COUNT],
    pub controls: SailControlState,
    pub interrupt_max_id: u64,
    pub interrupt_threshold: u64,
    pub interrupt_selector: u64,
    pub time_value: u64,
    pub time_ticks_per_second: u64,
    pub timer_deadline: u64,
    pub timer_interrupt_identity: u64,
    pub timer_armed: u8,
    pub fstatus: u64,
    pub fflags: u64,
    pub vstatus: u64,
    pub current_dfa: u8,
    pub supervisor: u8,
    pub halted: u8,
    pub run_state: i32,
    pub fp_enabled: u8,
    pub fp16_convert_enabled: u8,
    pub fptrans_enabled: u8,
    pub vector_enabled: u8,
    pub cache_maintenance_granule: i64,
    pub fp_state_modified: u8,
    pub vector_state_modified: u8,
    pub max_vector_length_bytes: i64,
    pub machine_check_error_code: u64,
    pub machine_check_event_aux: u64,
    pub machine_check_fault_ea: u64,
    pub machine_check_fault_linear: u64,
    pub machine_check_payload: u64,
    pub machine_check_pending: u8,
    pub nmi_latched_source: u64,
    pub nmi_relatched: u8,
    pub nmi_relatched_source: u64,
    pub repeat_active: u8,
    pub repeat_body_start: u64,
    pub repeat_condition: u64,
    pub repeat_counter: u64,
    pub repeat_prefix_start: u64,
    pub repeat_remaining: u64,
    pub repeat_fixed_body: [u8; MAX_INSTRUCTION_BYTES],
    pub repeat_fixed_body_length: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SailCoreRequest {
    pub kind: i32,
    pub operation: i32,
    pub form_id: i32,
    pub access: i32,
    pub role: i32,
    pub domain: i32,
    pub segment: i64,
    pub segment_image: u64,
    pub width: i64,
    pub ordinal: i64,
    pub range_length: i64,
    pub range_wrap: bool,
    pub range_end_at_modulus: bool,
    pub effective_address: u64,
    pub linear_address: u64,
    pub value: u64,
    pub desired: u64,
    pub expected: u64,
    pub range_start: u64,
    pub range_end: u64,
    pub address_translation: bool,
    pub debug_validation: bool,
    pub debug_validated: bool,
    pub physical_address: u64,
    pub read_completion: i32,
    pub memory_cache_hint: i32,
    pub memory_ranges: Vec<SailCoreMemoryRange>,
    pub validation_ranges: Vec<SailCoreValidationRange>,
    pub commit_point: bool,
    pub memory_order: i64,
    pub cache_policy: i64,
    pub suppress_fault: bool,
    pub selector: u64,
    pub auxiliary: u64,
    pub body_length: i64,
    pub payload: Vec<u8>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
#[repr(C)]
pub struct SailCoreMemoryRange {
    pub effective_address: u64,
    pub linear_address: u64,
    pub width: i64,
    pub buffer_offset: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
#[repr(C)]
pub struct SailCoreValidationRange {
    pub effective_address: u64,
    pub linear_address: u64,
    pub width: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SailCoreNumericOperand {
    pub valid: bool,
    pub kind: i32,
    pub bits: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SailCoreNumericRequest {
    pub valid: bool,
    pub shape: i32,
    pub path: i32,
    pub element_width: i64,
    pub lane_count: i64,
    pub result_kind: i32,
    pub operand_count: i64,
    pub operands: [SailCoreNumericOperand; 3],
    pub result_bytes: i64,
    pub predicate_bytes: i64,
    pub rounding_mode: u64,
    pub daz: bool,
    pub ftz: bool,
    pub dn: bool,
    pub allowed_causes: u64,
    pub flags_mask: u64,
    pub transcendental: bool,
    pub contract_id: u64,
    pub contract_word: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SailCoreNumericResponse {
    pub valid: bool,
    pub primary: u64,
    pub secondary: u64,
    pub primary_nan_origin: i32,
    pub secondary_nan_origin: i32,
    pub flags_mask: u8,
    pub flags_value: u8,
    pub generated_causes: u8,
    pub accuracy_mask: u8,
    pub error0_q8_8_up: u16,
    pub error1_q8_8_up: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SailCoreResponse {
    pub kind: i32,
    pub success: bool,
    pub fault_kind: i32,
    pub fault_cause: i64,
    pub fault_range: Option<SailCoreValidationRange>,
    pub detail: String,
    pub value: u64,
    pub secondary_value: u64,
    pub flags: u8,
    pub write_flags: bool,
    pub generated_fflags: u8,
    pub bounds_passed: bool,
    pub cache_policy: i64,
    pub access_class: i32,
    pub physical_class: i32,
    pub atomic_store_happened: bool,
    pub body: Vec<u8>,
    pub known: bool,
    pub present: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SailCoreEnvironmentState {
    pub cycle_counter: u64,
    pub retired_instruction_counter: u64,
    pub page_walk_counter: u64,
}

impl SailCoreEnvironmentState {
    fn page_walk_started(&mut self, enabled: bool) {
        if enabled {
            self.page_walk_counter = self.page_walk_counter.wrapping_add(1);
        }
    }
}

#[derive(Clone, Copy, Default)]
#[repr(C)]
struct RawSailCoreRequest {
    kind: i32,
    operation: i32,
    form_id: i32,
    access: i32,
    role: i32,
    domain: i32,
    segment: i64,
    segment_image: u64,
    width: i64,
    ordinal: i64,
    range_length: i64,
    range_wrap: u8,
    range_end_at_modulus: u8,
    effective_address: u64,
    linear_address: u64,
    value: u64,
    desired: u64,
    expected: u64,
    range_start: u64,
    range_end: u64,
    address_translation: u8,
    debug_validation: u8,
    debug_validated: u8,
    physical_address: u64,
    read_completion: i32,
    memory_cache_hint: i32,
    memory_range_count: usize,
    validation_range_count: usize,
    commit_point: u8,
    memory_order: i64,
    cache_policy: i64,
    suppress_fault: u8,
    selector: u64,
    auxiliary: u64,
    body_length: i64,
    payload_length: usize,
}

#[derive(Clone, Copy)]
#[repr(C)]
struct RawSailCoreResponse {
    kind: i32,
    success: u8,
    fault_kind: i32,
    fault_cause: i64,
    fault_range_present: u8,
    fault_range: SailCoreValidationRange,
    detail: *const c_char,
    value: u64,
    secondary_value: u64,
    flags: u8,
    write_flags: u8,
    generated_fflags: u8,
    bounds_passed: u8,
    cache_policy: i64,
    access_class: i32,
    physical_class: i32,
    atomic_store_happened: u8,
    body_bytes: *const u8,
    body_length: usize,
    known: u8,
    present: u8,
}

impl RawSailCoreRequest {
    fn into_request(
        self,
        payload: Vec<u8>,
        memory_ranges: Vec<SailCoreMemoryRange>,
        validation_ranges: Vec<SailCoreValidationRange>,
    ) -> SailCoreRequest {
        SailCoreRequest {
            kind: self.kind,
            operation: self.operation,
            form_id: self.form_id,
            access: self.access,
            role: self.role,
            domain: self.domain,
            segment: self.segment,
            segment_image: self.segment_image,
            width: self.width,
            ordinal: self.ordinal,
            range_length: self.range_length,
            range_wrap: self.range_wrap != 0,
            range_end_at_modulus: self.range_end_at_modulus != 0,
            effective_address: self.effective_address,
            linear_address: self.linear_address,
            value: self.value,
            desired: self.desired,
            expected: self.expected,
            range_start: self.range_start,
            range_end: self.range_end,
            address_translation: self.address_translation != 0,
            debug_validation: self.debug_validation != 0,
            debug_validated: self.debug_validated != 0,
            physical_address: self.physical_address,
            read_completion: self.read_completion,
            memory_cache_hint: self.memory_cache_hint,
            memory_ranges,
            validation_ranges,
            commit_point: self.commit_point != 0,
            memory_order: self.memory_order,
            cache_policy: self.cache_policy,
            suppress_fault: self.suppress_fault != 0,
            selector: self.selector,
            auxiliary: self.auxiliary,
            body_length: self.body_length,
            payload,
        }
    }
}

impl RawSailCoreResponse {
    fn from_response(response: &SailCoreResponse, detail: *const c_char) -> Self {
        Self {
            kind: response.kind,
            success: response.success.into(),
            fault_kind: response.fault_kind,
            fault_cause: response.fault_cause,
            fault_range_present: response.fault_range.is_some().into(),
            fault_range: response.fault_range.unwrap_or_default(),
            detail,
            value: response.value,
            secondary_value: response.secondary_value,
            flags: response.flags,
            write_flags: response.write_flags.into(),
            generated_fflags: response.generated_fflags,
            bounds_passed: response.bounds_passed.into(),
            cache_policy: response.cache_policy,
            access_class: response.access_class,
            physical_class: response.physical_class,
            atomic_store_happened: response.atomic_store_happened.into(),
            body_bytes: response.body.as_ptr(),
            body_length: response.body.len(),
            known: response.known.into(),
            present: response.present.into(),
        }
    }
}

pub struct SailCore {
    raw: *mut c_void,
    environment: SailCoreEnvironmentState,
}

fn apply_platform_features(state: &mut SailCoreState) {
    state.fp_enabled = platform::FEATURES.fp.into();
    state.fp16_convert_enabled = platform::FEATURES.fp16_convert.into();
    state.fptrans_enabled = platform::FEATURES.fptransa.into();
    state.vector_enabled = platform::FEATURES.vector.into();
}

// The core pointer is exclusively owned by `SailCore`. Moving that ownership
// to an execution thread is safe; all access still requires `&self`/`&mut self`
// and the generated Sail runtime is serialized by `SAIL_RUNTIME`.
unsafe impl Send for SailCore {}

impl fmt::Debug for SailCore {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.debug_struct("SailCore").finish_non_exhaustive()
    }
}

impl SailCore {
    pub fn new() -> Result<Self, SailCoreCreateError> {
        let raw = {
            let _runtime = runtime_lock();
            let state_size = unsafe { ffi::bedrock_core_state_size() };
            assert_eq!(
                state_size,
                std::mem::size_of::<SailCoreState>(),
                "emulator-core state ABI layout mismatch"
            );
            let raw = unsafe { ffi::bedrock_core_create() };
            if raw.is_null() {
                return Err(SailCoreCreateError::Status(SailCoreStatus::OutOfMemory));
            }
            raw
        };
        let mut core = Self {
            raw,
            environment: SailCoreEnvironmentState::default(),
        };
        let mut state = core.state().map_err(SailCoreCreateError::Status)?;
        apply_platform_features(&mut state);
        match core.set_state(state) {
            SailCoreStatus::Ok => Ok(core),
            status => Err(SailCoreCreateError::Status(status)),
        }
    }

    pub fn try_clone(&self) -> Result<Self, SailCoreCreateError> {
        let _runtime = runtime_lock();
        let raw = unsafe { ffi::bedrock_core_clone(self.raw) };
        if raw.is_null() {
            return Err(SailCoreCreateError::Status(SailCoreStatus::OutOfMemory));
        }
        Ok(Self {
            raw,
            environment: self.environment,
        })
    }

    pub fn reset(&mut self) -> SailCoreStatus {
        let _runtime = runtime_lock();
        let status = SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_reset(self.raw) });
        if status == SailCoreStatus::Ok {
            self.environment = SailCoreEnvironmentState::default();
        }
        status
    }
    pub fn pc(&self) -> Result<u64, SailCoreStatus> {
        let _runtime = runtime_lock();
        read_value(self.raw, |raw, value| unsafe {
            ffi::bedrock_core_get_pc(raw, value)
        })
    }
    pub fn set_pc(&mut self, value: u64) -> SailCoreStatus {
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_set_pc(self.raw, value) })
    }
    pub fn sp(&self) -> Result<u64, SailCoreStatus> {
        let _runtime = runtime_lock();
        read_value(self.raw, |raw, value| unsafe {
            ffi::bedrock_core_get_sp(raw, value)
        })
    }
    pub fn set_sp(&mut self, value: u64) -> SailCoreStatus {
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_set_sp(self.raw, value) })
    }
    pub fn register(&self, index: u32) -> Result<u64, SailCoreStatus> {
        let _runtime = runtime_lock();
        read_value(self.raw, |raw, value| unsafe {
            ffi::bedrock_core_get_register(raw, index, value)
        })
    }
    pub fn set_register(&mut self, index: u32, value: u64) -> SailCoreStatus {
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_set_register(self.raw, index, value) })
    }
    pub fn status(&self) -> Result<u64, SailCoreStatus> {
        let _runtime = runtime_lock();
        read_value(self.raw, |raw, value| unsafe {
            ffi::bedrock_core_get_status(raw, value)
        })
    }
    pub fn control(&self, selector: u32) -> Result<u64, SailCoreStatus> {
        let _runtime = runtime_lock();
        read_value(self.raw, |raw, value| unsafe {
            ffi::bedrock_core_get_control(raw, selector, value)
        })
    }
    pub fn post_interrupt(&mut self, identity: u32) -> SailCoreStatus {
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_post_interrupt(self.raw, identity) })
    }
    pub fn advance_time(&mut self, ticks: u64) -> SailCoreStatus {
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_advance_time(self.raw, ticks) })
    }
    pub fn is_supervisor(&self) -> Result<bool, SailCoreStatus> {
        let _runtime = runtime_lock();
        let mut value = 0;
        let status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_is_supervisor(self.raw, &mut value)
        });
        if status == SailCoreStatus::Ok {
            Ok(value != 0)
        } else {
            Err(status)
        }
    }
    pub fn state(&self) -> Result<SailCoreState, SailCoreStatus> {
        let _runtime = runtime_lock();
        let mut state = SailCoreState::default();
        let status =
            SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_get_state(self.raw, &mut state) });
        if status == SailCoreStatus::Ok {
            Ok(state)
        } else {
            Err(status)
        }
    }
    pub fn set_state(&mut self, state: SailCoreState) -> SailCoreStatus {
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_set_state(self.raw, &state) })
    }
    pub fn execute(&mut self, bytes: &[u8]) -> SailCoreStatus {
        if bytes.is_empty() || bytes.len() > MAX_INSTRUCTION_BYTES {
            return SailCoreStatus::BadArgument;
        }
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_execute(self.raw, bytes.as_ptr(), bytes.len())
        })
    }
    pub fn last_fault(&self) -> Result<SailCoreFault, SailCoreStatus> {
        let _runtime = runtime_lock();
        let mut fault = SailCoreFault::default();
        let status =
            SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_last_fault(self.raw, &mut fault) });
        if status == SailCoreStatus::Fault {
            Ok(fault)
        } else {
            Err(status)
        }
    }
    pub fn last_request(&self) -> Result<SailCoreRequest, SailCoreStatus> {
        let _runtime = runtime_lock();
        let mut request = RawSailCoreRequest::default();
        let status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_last_request(self.raw, &mut request)
        });
        if status != SailCoreStatus::NeedsEnvironment {
            return Err(status);
        }
        let mut payload_length = 0;
        let payload_status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_request_payload(
                self.raw,
                std::ptr::null_mut(),
                0,
                &mut payload_length,
            )
        });
        if payload_status != SailCoreStatus::NeedsEnvironment {
            return Err(payload_status);
        }
        let mut payload = vec![0; payload_length];
        let payload_status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_request_payload(
                self.raw,
                payload.as_mut_ptr(),
                payload.len(),
                &mut payload_length,
            )
        });
        if payload_status != SailCoreStatus::NeedsEnvironment {
            return Err(payload_status);
        }
        let mut range_count = 0;
        let range_status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_request_memory_ranges(
                self.raw,
                std::ptr::null_mut(),
                0,
                &mut range_count,
            )
        });
        if range_status != SailCoreStatus::NeedsEnvironment {
            return Err(range_status);
        }
        let mut memory_ranges = vec![SailCoreMemoryRange::default(); range_count];
        let range_status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_request_memory_ranges(
                self.raw,
                memory_ranges.as_mut_ptr(),
                memory_ranges.len(),
                &mut range_count,
            )
        });
        if range_status != SailCoreStatus::NeedsEnvironment {
            return Err(range_status);
        }
        let mut validation_count = 0;
        let validation_status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_request_validation_ranges(
                self.raw, std::ptr::null_mut(), 0, &mut validation_count,
            )
        });
        if validation_status != SailCoreStatus::NeedsEnvironment {
            return Err(validation_status);
        }
        let mut validation_ranges = vec![SailCoreValidationRange::default(); validation_count];
        let validation_status = SailCoreStatus::from_raw(unsafe {
            ffi::bedrock_core_request_validation_ranges(
                self.raw, validation_ranges.as_mut_ptr(), validation_ranges.len(),
                &mut validation_count,
            )
        });
        if validation_status != SailCoreStatus::NeedsEnvironment {
            return Err(validation_status);
        }
        Ok(request.into_request(payload, memory_ranges, validation_ranges))
    }
    pub fn resume(&mut self, response: SailCoreResponse) -> SailCoreStatus {
        let detail = match CString::new(response.detail.as_str()) {
            Ok(detail) => detail,
            Err(_) => return SailCoreStatus::BadArgument,
        };
        let raw_response = RawSailCoreResponse::from_response(&response, detail.as_ptr());
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_resume(self.raw, &raw_response) })
    }
    pub fn cancel(&mut self) -> SailCoreStatus {
        let _runtime = runtime_lock();
        SailCoreStatus::from_raw(unsafe { ffi::bedrock_core_cancel(self.raw) })
    }

    pub fn environment_state(&self) -> SailCoreEnvironmentState {
        self.environment
    }

    pub fn set_environment_state(&mut self, state: SailCoreEnvironmentState) {
        self.environment = state;
    }
}

impl Drop for SailCore {
    fn drop(&mut self) {
        let _runtime = runtime_lock();
        unsafe { ffi::bedrock_core_destroy(self.raw) };
    }
}

fn read_value(
    raw: *mut c_void,
    call: impl FnOnce(*mut c_void, *mut u64) -> i32,
) -> Result<u64, SailCoreStatus> {
    let mut value = 0;
    match SailCoreStatus::from_raw(call(raw, &mut value)) {
        SailCoreStatus::Ok => Ok(value),
        other => Err(other),
    }
}

mod ffi {
    use std::ffi::c_void;
    unsafe extern "C" {
        pub fn bedrock_core_state_size() -> usize;
        pub fn bedrock_core_create() -> *mut c_void;
        pub fn bedrock_core_clone(source: *mut c_void) -> *mut c_void;
        pub fn bedrock_core_destroy(core: *mut c_void);
        pub fn bedrock_core_reset(core: *mut c_void) -> i32;
        pub fn bedrock_core_get_pc(core: *mut c_void, value: *mut u64) -> i32;
        pub fn bedrock_core_set_pc(core: *mut c_void, value: u64) -> i32;
        pub fn bedrock_core_get_sp(core: *mut c_void, value: *mut u64) -> i32;
        pub fn bedrock_core_set_sp(core: *mut c_void, value: u64) -> i32;
        pub fn bedrock_core_get_register(core: *mut c_void, index: u32, value: *mut u64) -> i32;
        pub fn bedrock_core_set_register(core: *mut c_void, index: u32, value: u64) -> i32;
        pub fn bedrock_core_get_status(core: *mut c_void, value: *mut u64) -> i32;
        pub fn bedrock_core_get_control(core: *mut c_void, selector: u32, value: *mut u64) -> i32;
        pub fn bedrock_core_post_interrupt(core: *mut c_void, identity: u32) -> i32;
        pub fn bedrock_core_advance_time(core: *mut c_void, ticks: u64) -> i32;
        pub fn bedrock_core_is_supervisor(core: *mut c_void, value: *mut u8) -> i32;
        pub fn bedrock_core_get_state(core: *mut c_void, state: *mut super::SailCoreState) -> i32;
        pub fn bedrock_core_set_state(core: *mut c_void, state: *const super::SailCoreState)
        -> i32;
        pub fn bedrock_core_execute(core: *mut c_void, bytes: *const u8, length: usize) -> i32;
        pub fn bedrock_core_last_fault(core: *mut c_void, fault: *mut super::SailCoreFault) -> i32;
        pub fn bedrock_core_last_request(
            core: *mut c_void,
            request: *mut super::RawSailCoreRequest,
        ) -> i32;
        pub fn bedrock_core_request_payload(
            core: *mut c_void,
            buffer: *mut u8,
            capacity: usize,
            length: *mut usize,
        ) -> i32;
        pub fn bedrock_core_request_memory_ranges(
            core: *mut c_void,
            buffer: *mut super::SailCoreMemoryRange,
            capacity: usize,
            count: *mut usize,
        ) -> i32;
        pub fn bedrock_core_request_validation_ranges(
            core: *mut c_void,
            buffer: *mut super::SailCoreValidationRange,
            capacity: usize,
            count: *mut usize,
        ) -> i32;
        pub fn bedrock_core_cancel(core: *mut c_void) -> i32;
        pub fn bedrock_core_resume(
            core: *mut c_void,
            response: *const super::RawSailCoreResponse,
        ) -> i32;
    }
}

#[cfg(test)]
mod tests {
    use super::{
        SailBusExecutionError, SailCore, SailCoreResponse, SailCoreStatus,
        protocol::{request_kind, response_kind},
    };
    use bedrock_bus::{Bus, BusResult, Ram};

    include!(concat!(env!("OUT_DIR"), "/instruction_test_records.rs"));
    const VGATHER_B_SCALAR_STRIDE: [u8; 6] = [0xcf, 0xfc, 0x10, 0x02, 0x02, 0x43];
    const VSCATTER_B_SCALAR_STRIDE: [u8; 6] = [0xcf, 0xfc, 0x18, 0x02, 0x02, 0x43];
    const SAVE_R0: [u8; 3] = [0xc3, 0xb5, 0x80];
    const RESTORE_R0: [u8; 3] = [0xc3, 0xb5, 0x90];
    const SSAVE_R0: [u8; 3] = [0xc3, 0xb5, 0xa0];
    const SRESTORE_R0: [u8; 3] = [0xc3, 0xb5, 0xb0];
    const FSAVE_R0: [u8; 3] = [0xc3, 0xb5, 0xc0];
    const FRESTORE_R0: [u8; 3] = [0xc3, 0xb5, 0xd0];
    const VSAVE_R0: [u8; 3] = [0xc3, 0xb5, 0xe0];

    struct NoMemoryBus;

    impl Bus for NoMemoryBus {
        fn begin_transaction(&mut self) -> BusResult<()> {
            Ok(())
        }

        fn commit_transaction(&mut self) {}

        fn rollback_transaction(&mut self) {}

        fn read_u8(&mut self, addr: u64) -> BusResult<u8> {
            panic!("resident control transfer read memory at {addr:#x}")
        }

        fn write_u8(&mut self, addr: u64, _value: u8) -> BusResult<()> {
            panic!("resident control transfer wrote memory at {addr:#x}")
        }
    }

    fn word(bytes: &[u8], offset: usize) -> u64 {
        u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap())
    }

    #[test]
    fn executes_generated_nop_and_exposes_register_state() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.pc(), Ok(0));
        assert_eq!(
            core.set_register(7, 0x55aa_55aa_55aa_55aa),
            SailCoreStatus::Ok
        );
        assert_eq!(core.register(7), Ok(0x55aa_55aa_55aa_55aa));
        assert_eq!(core.execute(&[0x01]), SailCoreStatus::Ok);
        assert_eq!(core.pc(), Ok(1));
    }

    #[test]
    fn resident_near_call_pair_preserves_the_ordinary_footprint_without_memory() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.registers[0] = 0x200;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &FCALL_R0).unwrap();
        let linked = core.state().unwrap();
        assert_eq!(linked.pc, 0x200);
        assert_eq!(linked.sp, 0x0ff8);
        assert_eq!(linked.lpc, 0x103);
        assert_eq!(linked.lpa, 0x8000_0000_0000_0000);

        core.execute_on_bus(&mut bus, &RET).unwrap();
        let returned = core.state().unwrap();
        assert_eq!(returned.pc, 0x103);
        assert_eq!(returned.sp, 0x1000);
        assert_eq!(returned.lpc, 0);
        assert_eq!(returned.lpa, 0);
    }

    #[test]
    fn resident_stack_footprint_may_end_at_the_address_space_modulus() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0;
        state.registers[0] = 0x200;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &FCALL_R0).unwrap();
        assert_eq!(core.sp(), Ok(u64::MAX - 7));
        core.execute_on_bus(&mut bus, &RET).unwrap();
        assert_eq!(core.sp(), Ok(0));
    }

    #[test]
    fn resident_stack_adjustments_reject_ranges_crossing_the_address_space_modulus() {
        let mut call = SailCore::new().unwrap();
        let mut before_call = call.state().unwrap();
        before_call.pc = 0x100;
        before_call.sp = 7;
        before_call.registers[0] = 0x200;
        assert_eq!(call.set_state(before_call.clone()), SailCoreStatus::Ok);
        let mut call_bus = NoMemoryBus;

        let call_fault = match call.execute_on_bus(&mut call_bus, &FCALL_R0) {
            Err(SailBusExecutionError::Fault { fault, .. }) => fault,
            other => panic!("unexpected FCALL result: {other:?}"),
        };
        assert_eq!(call_fault.kind, crate::fault_kind::BOUNDS_FAULT);
        assert_eq!(call_fault.error_code & 0xff, 4);
        assert_eq!(call.state(), Ok(before_call));

        let mut ret = SailCore::new().unwrap();
        let mut before_ret = ret.state().unwrap();
        before_ret.pc = 0x100;
        before_ret.sp = u64::MAX - 6;
        before_ret.lpc = 0x200;
        before_ret.lpa = 0x8000_0000_0000_0000;
        assert_eq!(ret.set_state(before_ret.clone()), SailCoreStatus::Ok);
        let mut ret_bus = NoMemoryBus;

        let ret_fault = match ret.execute_on_bus(&mut ret_bus, &RET) {
            Err(SailBusExecutionError::Fault { fault, .. }) => fault,
            other => panic!("unexpected RET result: {other:?}"),
        };
        assert_eq!(ret_fault.kind, crate::fault_kind::BOUNDS_FAULT);
        assert_eq!(ret_fault.error_code & 0xff, 4);
        assert_eq!(ret.state(), Ok(before_ret));
    }

    #[test]
    fn false_conditional_call_ignores_a_live_resident_link() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.pc = 0x100;
        before.sp = 0x1000;
        before.registers[0] = 0x200;
        before.lpc = 0x300;
        before.lpa = 0x8000_0000_0000_0000;
        let mut expected = before.clone();
        expected.pc += CALLCC_R0_FALSE.len() as u64;
        assert_eq!(core.set_state(before), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &CALLCC_R0_FALSE).unwrap();
        assert_eq!(core.state(), Ok(expected));
    }

    #[test]
    fn clear_link_changes_only_resident_state() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.pc = 0x100;
        before.sp = 0x1000;
        before.registers[3] = 0x1122_3344_5566_7788;
        before.lpc = 0x200;
        before.lpa = 0x8000_0000_0000_0042;
        before.lkl = 0x0123_4567_89ab_cdef;
        before.lkh = 0xfedc_ba98_7654_3210;
        let mut expected = before.clone();
        expected.pc += CLRLINK.len() as u64;
        expected.lpc = 0;
        expected.lpa = 0;
        assert_eq!(core.set_state(before), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &CLRLINK).unwrap();
        assert_eq!(core.state(), Ok(expected));
    }

    #[test]
    fn live_resident_link_rejects_a_taken_call_before_target_access() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.registers[0] = 0x200;
        before.lpc = 0x300;
        before.lpa = 0x8000_0000_0000_0000;
        assert_eq!(core.set_state(before.clone()), SailCoreStatus::Ok);

        assert_eq!(core.execute(&FCALL_R0), SailCoreStatus::Fault);
        assert_eq!(core.state(), Ok(before));
    }

    #[test]
    fn protected_call_rejects_an_unconfigured_key_before_target_access() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.registers[0] = 0x200;
        assert_eq!(core.set_state(before.clone()), SailCoreStatus::Ok);

        assert_eq!(core.execute(&FPCALL_R0), SailCoreStatus::Fault);
        assert_eq!(core.state(), Ok(before));
    }

    #[test]
    fn protected_resident_continuation_authenticates_at_its_reserved_slot() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &FPCALL_DISP16_ZERO).unwrap();
        let linked = core.state().unwrap();
        assert_eq!(linked.pc, 0x105);
        assert_eq!(linked.sp, 0x0ff0);
        assert_eq!(linked.lpc, 0x105);
        assert_eq!(linked.lpa >> 63, 1);
        assert_ne!(linked.lpa & 0x7fff_ffff_ffff_ffff, 0);

        core.execute_on_bus(&mut bus, &PRET).unwrap();
        let returned = core.state().unwrap();
        assert_eq!(returned.pc, 0x105);
        assert_eq!(returned.sp, 0x1000);
        assert_eq!(returned.lpc, 0);
        assert_eq!(returned.lpa, 0);
    }

    #[test]
    fn relocated_protected_resident_continuation_fails_without_state_change() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &FPCALL_DISP16_ZERO).unwrap();
        let mut relocated = core.state().unwrap();
        relocated.sp -= 16;
        assert_eq!(core.set_state(relocated.clone()), SailCoreStatus::Ok);

        let error = core.execute_on_bus(&mut bus, &PRET).unwrap_err();
        match error {
            SailBusExecutionError::Fault { fault, .. } => assert_eq!(fault.error_code, 2),
            other => panic!("unexpected PRET failure: {other}"),
        }
        assert_eq!(core.state(), Ok(relocated));
    }

    #[test]
    fn changed_key_invalidates_a_protected_resident_continuation() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &FPCALL_DISP16_ZERO).unwrap();
        let mut rekeyed = core.state().unwrap();
        rekeyed.lkl = 2;
        assert_eq!(core.set_state(rekeyed.clone()), SailCoreStatus::Ok);

        let error = core.execute_on_bus(&mut bus, &PRET).unwrap_err();
        match error {
            SailBusExecutionError::Fault { fault, .. } => assert_eq!(fault.error_code, 2),
            other => panic!("unexpected PRET failure: {other}"),
        }
        assert_eq!(core.state(), Ok(rekeyed));
    }

    #[test]
    fn modified_protected_stack_record_fails_without_state_change() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut ram = Ram::new(0x2000);

        core.execute_on_bus(&mut ram, &PCALL_DISP16_ZERO).unwrap();
        assert_eq!(core.sp(), Ok(0x0ff0));
        assert_eq!(Bus::read_u64(&mut ram, 0x0ff0).unwrap(), 0x105);
        let tag = Bus::read_u64(&mut ram, 0x0ff8).unwrap();
        let modified = if tag ^ 1 == 0 { 2 } else { tag ^ 1 };
        Bus::write_u64(&mut ram, 0x0ff8, modified).unwrap();
        let before_return = core.state().unwrap();

        let error = core.execute_on_bus(&mut ram, &PRET).unwrap_err();
        match error {
            SailBusExecutionError::Fault { fault, .. } => {
                assert_eq!(fault.error_code, 2);
            }
            other => panic!("unexpected PRET failure: {other}"),
        }
        assert_eq!(core.state(), Ok(before_return));
    }

    #[test]
    fn unprotected_return_from_materialized_protected_call_leaves_the_tag_word() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut ram = Ram::new(0x2000);

        core.execute_on_bus(&mut ram, &PCALL_DISP16_ZERO).unwrap();
        let tag = Bus::read_u64(&mut ram, 0x0ff8).unwrap();
        core.execute_on_bus(&mut ram, &RET).unwrap();

        assert_eq!(core.pc(), Ok(0x105));
        assert_eq!(core.sp(), Ok(0x0ff8));
        assert_eq!(Bus::read_u64(&mut ram, 0x0ff8).unwrap(), tag);
    }

    #[test]
    fn unprotected_return_from_resident_protected_call_restores_its_reserved_footprint() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut bus = NoMemoryBus;

        core.execute_on_bus(&mut bus, &FPCALL_DISP16_ZERO).unwrap();
        core.execute_on_bus(&mut bus, &RET).unwrap();

        assert_eq!(core.pc(), Ok(0x105));
        assert_eq!(core.sp(), Ok(0x1000));
        assert_eq!(core.state().unwrap().lpa, 0);
    }

    #[test]
    fn configured_indirect_call_requires_cfi_landing() {
        let configured_state = |core: &SailCore| {
            let mut state = core.state().unwrap();
            state.pc = 0x100;
            state.sp = 0x1000;
            state.registers[0] = 0x200;
            state.lkl = 1;
            state
        };

        let mut accepted = SailCore::new().unwrap();
        let accepted_state = configured_state(&accepted);
        assert_eq!(accepted.set_state(accepted_state), SailCoreStatus::Ok);
        let mut accepted_ram = Ram::new(0x1000);
        accepted_ram.load(0x200, &CFILAND).unwrap();
        accepted
            .execute_on_bus(&mut accepted_ram, &FPCALL_R0)
            .unwrap();
        assert_eq!(accepted.pc(), Ok(0x200));

        let mut rejected = SailCore::new().unwrap();
        let before = configured_state(&rejected);
        assert_eq!(rejected.set_state(before.clone()), SailCoreStatus::Ok);
        let mut rejected_ram = Ram::new(0x1000);
        let error = rejected
            .execute_on_bus(&mut rejected_ram, &FPCALL_R0)
            .unwrap_err();
        match error {
            SailBusExecutionError::Fault { fault, .. } => {
                assert_eq!(fault.error_code, 1);
            }
            other => panic!("unexpected FPCALL failure: {other}"),
        }
        assert_eq!(rejected.state(), Ok(before));
    }

    #[test]
    fn protected_long_record_round_trips_the_complete_continuation() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.segments[0] = 1;
        state.registers[0] = 0x200;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut ram = Ram::new(0x2000);
        ram.load(0x200, &CFILAND).unwrap();

        core.execute_on_bus(&mut ram, &PLCALL_R0_R0).unwrap();
        assert_eq!(core.pc(), Ok(0x200));
        assert_eq!(core.sp(), Ok(0x0fe8));
        assert_eq!(Bus::read_u64(&mut ram, 0x0fe8).unwrap(), 0x103);
        assert_eq!(Bus::read_u64(&mut ram, 0x0ff0).unwrap(), 1);
        assert_ne!(Bus::read_u64(&mut ram, 0x0ff8).unwrap(), 0);

        core.execute_on_bus(&mut ram, &PLRET).unwrap();
        assert_eq!(core.pc(), Ok(0x103));
        assert_eq!(core.sp(), Ok(0x1000));
        assert_eq!(core.state().unwrap().segments[0], 1);
    }

    #[test]
    fn modified_protected_long_cs_fails_without_state_change() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.pc = 0x100;
        state.sp = 0x1000;
        state.segments[0] = 1;
        state.registers[0] = 0x200;
        state.lkl = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut ram = Ram::new(0x2000);
        ram.load(0x200, &CFILAND).unwrap();

        core.execute_on_bus(&mut ram, &PLCALL_R0_R0).unwrap();
        Bus::write_u64(&mut ram, 0x0ff0, 2).unwrap();
        let before_return = core.state().unwrap();

        let error = core.execute_on_bus(&mut ram, &PLRET).unwrap_err();
        match error {
            SailBusExecutionError::Fault { fault, .. } => assert_eq!(fault.error_code, 2),
            other => panic!("unexpected PLRET failure: {other}"),
        }
        assert_eq!(core.state(), Ok(before_return));
    }

    #[test]
    fn cfi_supervisor_record_round_trips_only_the_key() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.supervisor = 1;
        state.status = 0x10;
        state.registers[0] = 0x100;
        state.lpc = 0x1122_3344_5566_7788;
        state.lpa = 0x8000_0000_0000_0042;
        state.lkl = 0x0123_4567_89ab_cdef;
        state.lkh = 0xfedc_ba98_7654_3210;
        let mut expected_after_save = state.clone();
        expected_after_save.pc += CFISSAVE_R0.len() as u64;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        let mut ram = Ram::new(0x1000);

        core.execute_on_bus(&mut ram, &CFISSAVE_R0).unwrap();
        assert_eq!(core.state(), Ok(expected_after_save));
        assert_eq!(
            Bus::read_u64(&mut ram, 0x100).unwrap(),
            0x0123_4567_89ab_cdef
        );
        assert_eq!(
            Bus::read_u64(&mut ram, 0x108).unwrap(),
            0xfedc_ba98_7654_3210
        );

        let mut changed = core.state().unwrap();
        changed.lkl = 7;
        changed.lkh = 9;
        assert_eq!(core.set_state(changed), SailCoreStatus::Ok);
        core.execute_on_bus(&mut ram, &CFISRESTORE_R0).unwrap();
        let restored = core.state().unwrap();
        assert_eq!(restored.lkl, 0x0123_4567_89ab_cdef);
        assert_eq!(restored.lkh, 0xfedc_ba98_7654_3210);
        assert_eq!(restored.lpc, 0x1122_3344_5566_7788);
        assert_eq!(restored.lpa, 0x8000_0000_0000_0042);
    }

    #[test]
    fn warm_reset_clears_resident_link_and_cfi_key_state() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.lpc = 1;
        state.lpa = 0x8000_0000_0000_0002;
        state.lkl = 3;
        state.lkh = 4;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        assert_eq!(core.reset(), SailCoreStatus::Ok);
        let reset = core.state().unwrap();
        assert_eq!(reset.lpc, 0);
        assert_eq!(reset.lpa, 0);
        assert_eq!(reset.lkl, 0);
        assert_eq!(reset.lkh, 0);
    }

    #[test]
    fn sub_quad_register_moves_zero_extend_the_result() {
        for (instruction, expected) in [
            ([0xa6, 0x01], 0x88),
            ([0xa7, 0x01], 0x7788),
            ([0x80, 0x01], 0x5566_7788),
        ] {
            let mut core = SailCore::new().unwrap();
            assert_eq!(
                core.set_register(0, 0x1122_3344_5566_7788),
                SailCoreStatus::Ok
            );
            assert_eq!(
                core.set_register(1, 0xffff_ffff_ffff_ffff),
                SailCoreStatus::Ok
            );
            assert_eq!(core.execute(&instruction), SailCoreStatus::Ok);
            assert_eq!(core.register(1), Ok(expected));
        }
    }

    #[test]
    fn register_add_executes_through_the_uop_program() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(
            core.set_register(0, 0xffff_ffff_0000_0002),
            SailCoreStatus::Ok
        );
        assert_eq!(
            core.set_register(1, 0xffff_ffff_0000_0003),
            SailCoreStatus::Ok
        );

        // ADD.L R0, R1
        assert_eq!(core.execute(&[0x82, 0x01]), SailCoreStatus::Ok);
        assert_eq!(core.register(1), Ok(5));
        assert_eq!(core.pc(), Ok(2));
    }

    #[test]
    fn timebase_reads_advance_only_by_explicit_host_ticks() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.time_value = u64::MAX - 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // RDTIME R3
        assert_eq!(core.execute(&[0xc3, 0xb4, 0x83]), SailCoreStatus::Ok);
        assert_eq!(core.register(3), Ok(u64::MAX - 1));
        assert_eq!(core.state().unwrap().time_value, u64::MAX - 1);

        assert_eq!(core.advance_time(3), SailCoreStatus::Ok);
        // RDTIME R4
        assert_eq!(core.execute(&[0xc3, 0xb4, 0x84]), SailCoreStatus::Ok);
        assert_eq!(core.register(4), Ok(1));
    }

    #[test]
    fn deadline_timer_posts_at_a_wrapped_deadline_and_disarms() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.time_value = u64::MAX - 2;
        state.registers[1] = 2;
        state.registers[2] = 5;
        state.supervisor = 1;
        state.status |= 1 << 4;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // TARM R1, R2
        assert_eq!(core.execute(&[0xc3, 0x04, 0x12]), SailCoreStatus::Ok);
        assert_eq!(core.state().unwrap().timer_armed, 1);

        assert_eq!(core.advance_time(4), SailCoreStatus::Ok);
        assert_eq!(core.state().unwrap().time_value, 1);
        assert_eq!(core.state().unwrap().timer_armed, 1);
        assert_eq!(core.control(0x0304), Ok(0));

        assert_eq!(core.advance_time(1), SailCoreStatus::Ok);
        assert_eq!(core.state().unwrap().time_value, 2);
        assert_eq!(core.state().unwrap().timer_armed, 0);
        assert_eq!(core.control(0x0304), Ok(1 << 5));
    }

    #[test]
    fn half_range_deadline_is_already_reached_and_posts_without_arming() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.time_value = 10;
        state.timer_deadline = 100;
        state.timer_interrupt_identity = 7;
        state.timer_armed = 1;
        state.registers[1] = 10 + (1 << 63);
        state.registers[2] = 6;
        state.supervisor = 1;
        state.status |= 1 << 4;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // TARM R1, R2
        assert_eq!(core.execute(&[0xc3, 0x04, 0x12]), SailCoreStatus::Ok);
        assert_eq!(core.state().unwrap().timer_armed, 0);
        assert_eq!(core.control(0x0304), Ok(1 << 6));
    }

    #[test]
    fn timer_cancel_disarms_without_clearing_a_pending_identity() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.post_interrupt(5), SailCoreStatus::Ok);
        let mut state = core.state().unwrap();
        state.timer_deadline = 100;
        state.timer_interrupt_identity = 7;
        state.timer_armed = 1;
        state.supervisor = 1;
        state.status |= 1 << 4;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // TCANCEL
        assert_eq!(core.execute(&[0xae, 0x02]), SailCoreStatus::Ok);
        assert_eq!(core.state().unwrap().timer_armed, 0);
        assert_eq!(core.control(0x0304), Ok(1 << 5));
    }

    #[test]
    fn invalid_timer_identity_faults_without_replacing_the_arm() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.timer_deadline = 100;
        state.timer_interrupt_identity = 7;
        state.timer_armed = 1;
        state.registers[1] = 200;
        state.registers[2] = 1 << 24;
        state.supervisor = 1;
        state.status |= 1 << 4;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        // TARM R1, R2
        assert_eq!(core.execute(&[0xc3, 0x04, 0x12]), SailCoreStatus::Fault);
        let state = core.state().unwrap();
        assert_eq!(state.timer_deadline, 100);
        assert_eq!(state.timer_interrupt_identity, 7);
        assert_eq!(state.timer_armed, 1);
    }

    #[test]
    fn warm_reset_preserves_time_and_disarms_the_timer() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        let ticks_per_second = state.time_ticks_per_second;
        state.time_value = 0x1122_3344_5566_7788;
        state.timer_deadline = 0x2233_4455_6677_8899;
        state.timer_interrupt_identity = 9;
        state.timer_armed = 1;
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        assert_eq!(core.reset(), SailCoreStatus::Ok);
        let state = core.state().unwrap();
        assert_eq!(state.time_value, 0x1122_3344_5566_7788);
        assert_eq!(state.time_ticks_per_second, ticks_per_second);
        assert_eq!(state.timer_armed, 0);
    }

    #[test]
    fn register_integer_alu_operations_execute_through_uops() {
        for (opcode, expected) in [
            (0x84, 0x24bd),
            (0x88, 0x030c),
            (0x8a, 0x3fcf),
            (0x8c, 0x3cc3),
        ] {
            let mut core = SailCore::new().unwrap();
            assert_eq!(core.set_register(0, 0x0f0f), SailCoreStatus::Ok);
            assert_eq!(core.set_register(1, 0x33cc), SailCoreStatus::Ok);

            assert_eq!(core.execute(&[opcode, 0x01]), SailCoreStatus::Ok);
            assert_eq!(core.register(1), Ok(expected));
            assert_eq!(core.pc(), Ok(2));
        }
    }

    #[test]
    fn register_integer_unary_operations_execute_through_uops() {
        for (instruction, expected) in [
            ([0xa8, 0x51], 0x0000_0000_ffff_f0f0),
            ([0xa8, 0x21], 0x0000_0000_ffff_f0f1),
        ] {
            let mut core = SailCore::new().unwrap();
            assert_eq!(core.set_register(1, 0x0f0f), SailCoreStatus::Ok);

            assert_eq!(core.execute(&instruction), SailCoreStatus::Ok);
            assert_eq!(core.register(1), Ok(expected));
            assert_eq!(core.pc(), Ok(2));
        }
    }

    #[test]
    fn register_compare_and_test_commit_generated_flags() {
        let mut compare = SailCore::new().unwrap();
        assert_eq!(compare.set_register(0, 2), SailCoreStatus::Ok);
        assert_eq!(compare.set_register(1, 1), SailCoreStatus::Ok);
        assert_eq!(compare.execute(&[0x86, 0x01]), SailCoreStatus::Ok);
        assert_eq!(compare.state().unwrap().flags, 0x6);

        let mut test = SailCore::new().unwrap();
        assert_eq!(test.set_register(0, 0x0f), SailCoreStatus::Ok);
        assert_eq!(test.set_register(1, 0xf0), SailCoreStatus::Ok);
        assert_eq!(test.execute(&[0x8e, 0x01]), SailCoreStatus::Ok);
        assert_eq!(test.state().unwrap().flags, 0x8);
    }

    #[test]
    fn memory_move_resumes_the_uop_program_after_a_load() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.set_register(0, 0x100), SailCoreStatus::Ok);

        // MOV.B [R0], R0
        assert_eq!(
            core.execute(&[0xc1, 0x00, 0x00]),
            SailCoreStatus::NeedsEnvironment
        );
        let request = core.last_request().unwrap();
        assert_eq!(request.kind, request_kind::READ);
        assert_eq!(request.effective_address, 0x100);
        assert_eq!(request.width, 1);

        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: vec![0xa5],
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Ok
        );
        assert_eq!(core.register(0), Ok(0xa5));
        assert_eq!(core.pc(), Ok(3));
    }

    #[test]
    fn invalid_response_representation_preserves_pending_execution() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.set_register(0, 0x100), SailCoreStatus::Ok);
        assert_eq!(
            core.execute(&[0xc1, 0x00, 0x00]),
            SailCoreStatus::NeedsEnvironment
        );
        let request = core.last_request().unwrap();
        let state = core.state().unwrap();
        let valid = SailCoreResponse {
            kind: response_kind::READ,
            success: true,
            body: vec![0xa5],
            known: true,
            present: true,
            ..SailCoreResponse::default()
        };

        let mut invalid_responses = Vec::new();
        let mut invalid = valid.clone();
        invalid.kind = i32::MAX;
        invalid_responses.push(invalid);
        let mut invalid = valid.clone();
        invalid.fault_kind = i32::MAX;
        invalid_responses.push(invalid);
        let mut invalid = valid.clone();
        invalid.access_class = 2;
        invalid_responses.push(invalid);
        let mut invalid = valid.clone();
        invalid.physical_class = 2;
        invalid_responses.push(invalid);
        let mut invalid = valid.clone();
        invalid.flags = 0x10;
        invalid_responses.push(invalid);
        let mut invalid = valid.clone();
        invalid.generated_fflags = 0x20;
        invalid_responses.push(invalid);

        for invalid in invalid_responses {
            assert_eq!(core.resume(invalid), SailCoreStatus::BadArgument);
            assert_eq!(core.last_request(), Ok(request.clone()));
            assert_eq!(core.state(), Ok(state.clone()));
        }

        assert_eq!(core.resume(valid), SailCoreStatus::Ok);
        assert_eq!(core.register(0), Ok(0xa5));
        assert_eq!(core.pc(), Ok(3));
    }

    #[test]
    fn ea_postincrement_is_an_explicit_speculative_uop() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.set_register(2, 0x100), SailCoreStatus::Ok);
        assert_eq!(core.set_register(3, 4), SailCoreStatus::Ok);

        // MOV.B [DS:R2 + R3++], R0
        assert_eq!(
            core.execute(&[0xc9, 0x00, 0x68, 0x80, 0x23]),
            SailCoreStatus::NeedsEnvironment
        );
        let request = core.last_request().unwrap();
        assert_eq!(request.effective_address, 0x104);
        // The postincrement remains speculative until the instruction commits.
        assert_eq!(core.register(3), Ok(4));

        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: vec![0x7b],
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Ok
        );
        assert_eq!(core.register(0), Ok(0x7b));
        assert_eq!(core.register(3), Ok(5));
        assert_eq!(core.pc(), Ok(5));
    }

    #[test]
    fn resumes_push_through_environment_requests() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.set_sp(0x1000), SailCoreStatus::Ok);
        assert_eq!(
            core.set_register(0, 0x1122_3344_5566_7788),
            SailCoreStatus::Ok
        );
        assert_eq!(core.execute(&[0x30]), SailCoreStatus::NeedsEnvironment);

        let mut status = SailCoreStatus::NeedsEnvironment;
        for _ in 0..8 {
            if status != SailCoreStatus::NeedsEnvironment {
                break;
            }
            let request = core.last_request().unwrap();
            let response_kind = match request.kind {
                request_kind::STACK_RANGE => {
                    assert!(request.payload.is_empty());
                    response_kind::STACK_RANGE
                }
                request_kind::MEMORY_PROBE => {
                    assert!(request.payload.is_empty());
                    response_kind::PROBE
                }
                request_kind::WRITE => {
                    assert_eq!(request.payload, 0x1122_3344_5566_7788u64.to_le_bytes());
                    response_kind::WRITE
                }
                kind => panic!("unexpected PUSH environment request {kind}"),
            };
            status = core.resume(SailCoreResponse {
                kind: response_kind,
                success: true,
                bounds_passed: true,
                known: true,
                present: true,
                ..SailCoreResponse::default()
            });
        }

        assert_eq!(status, SailCoreStatus::Ok);
        assert_eq!(core.pc(), Ok(1));
        assert_eq!(core.sp(), Ok(0x0ff8));
        assert_eq!(
            core.resume(SailCoreResponse::default()),
            SailCoreStatus::BadState
        );
    }

    #[test]
    fn cancelling_pending_execution_restores_idle_state() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.set_sp(0x1000), SailCoreStatus::Ok);
        assert_eq!(core.execute(&[0x30]), SailCoreStatus::NeedsEnvironment);
        assert_eq!(core.cancel(), SailCoreStatus::Ok);
        assert_eq!(core.cancel(), SailCoreStatus::BadState);
        assert_eq!(core.execute(&[0x01]), SailCoreStatus::Ok);
        assert_eq!(core.pc(), Ok(1));
        assert_eq!(core.sp(), Ok(0x1000));
    }

    #[test]
    fn resumable_gather_commits_one_lane_at_each_vector_boundary() {
        let mut core = SailCore::new().unwrap();
        let mut state = core.state().unwrap();
        state.registers[1] = 0x100;
        state.registers[2] = 1;
        state.predicate_registers[0] = [0x05, 0x00];
        state.predicate_registers[1] = [0xf2, 0xff];
        state.vector_registers[3].fill(0xcc);
        assert_eq!(core.set_state(state), SailCoreStatus::Ok);

        assert_eq!(
            core.execute(&VGATHER_B_SCALAR_STRIDE),
            SailCoreStatus::NeedsEnvironment
        );
        let first = core.last_request().unwrap();
        assert_eq!(first.kind, request_kind::READ);
        assert_eq!(first.effective_address, 0x100);
        assert_eq!(first.width, 1);
        assert_eq!(first.body_length, 1);
        assert!(first.payload.is_empty());
        assert_eq!(core.state().unwrap().predicate_registers[1], [0x00, 0x00]);

        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: vec![0x11],
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::VectorLane
        );
        let state = core.state().unwrap();
        assert_eq!(state.vector_registers[3][0], 0x11);
        assert_eq!(state.vector_registers[3][2], 0xcc);
        assert_eq!(state.predicate_registers[1], [0x01, 0x00]);
        assert_eq!(state.pc, 0);

        assert_eq!(
            core.execute(&VGATHER_B_SCALAR_STRIDE),
            SailCoreStatus::NeedsEnvironment
        );
        let second = core.last_request().unwrap();
        assert_eq!(second.kind, request_kind::READ);
        assert_eq!(second.effective_address, 0x102);
        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: vec![0x33],
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Ok
        );
        let state = core.state().unwrap();
        assert_eq!(state.vector_registers[3][0], 0x11);
        assert_eq!(state.vector_registers[3][2], 0x33);
        assert_eq!(state.predicate_registers[1], [0x05, 0x00]);
        assert_eq!(state.pc, VGATHER_B_SCALAR_STRIDE.len() as u64);
    }

    #[test]
    fn resumable_scatter_distinguishes_pre_and_post_store_failures() {
        let mut retryable = SailCore::new().unwrap();
        let mut state = retryable.state().unwrap();
        state.registers[1] = 0x100;
        state.registers[2] = 1;
        state.predicate_registers[0] = [0x01, 0x00];
        state.predicate_registers[1] = [0xfe, 0xff];
        state.vector_registers[3][0] = 0xa5;
        assert_eq!(retryable.set_state(state), SailCoreStatus::Ok);

        assert_eq!(
            retryable.execute(&VSCATTER_B_SCALAR_STRIDE),
            SailCoreStatus::NeedsEnvironment
        );
        let request = retryable.last_request().unwrap();
        assert_eq!(request.kind, request_kind::WRITE);
        assert_eq!(request.effective_address, 0x100);
        assert_eq!(request.selector, 1);
        assert_eq!(request.payload, vec![0xa5]);
        assert_eq!(
            retryable.resume(SailCoreResponse {
                kind: response_kind::WRITE,
                success: false,
                fault_kind: crate::translation::FAULT_ACCESS,
                fault_cause: 7,
                fault_range: request.validation_ranges.first().copied(),
                detail: "store failed before the point of no return".to_owned(),
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Fault
        );
        let state = retryable.state().unwrap();
        assert_eq!(state.predicate_registers[1], [0x00, 0x00]);
        assert_eq!(state.machine_check_pending, 0);
        assert_eq!(state.pc, 0);

        let mut irrevocable = SailCore::new().unwrap();
        let mut state = irrevocable.state().unwrap();
        state.registers[1] = 0x100;
        state.registers[2] = 1;
        state.predicate_registers[0] = [0x01, 0x00];
        state.vector_registers[3][0] = 0x5a;
        assert_eq!(irrevocable.set_state(state), SailCoreStatus::Ok);
        assert_eq!(
            irrevocable.execute(&VSCATTER_B_SCALAR_STRIDE),
            SailCoreStatus::NeedsEnvironment
        );
        assert_eq!(
            irrevocable.resume(SailCoreResponse {
                kind: response_kind::WRITE,
                success: false,
                fault_kind: crate::translation::FAULT_ACCESS,
                fault_cause: 7,
                detail: "store failed after the point of no return".to_owned(),
                fault_range: irrevocable.last_request().unwrap().validation_ranges.first().copied(),
                atomic_store_happened: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Ok
        );
        let state = irrevocable.state().unwrap();
        assert_eq!(state.predicate_registers[1], [0x01, 0x00]);
        assert_eq!(state.machine_check_pending, 1);
        assert_eq!(state.machine_check_error_code, 0x0000_0000_0700_0104);
        assert_eq!(state.machine_check_fault_ea, 0x100);
        assert_eq!(state.machine_check_payload, 7);
        assert_eq!(state.pc, VSCATTER_B_SCALAR_STRIDE.len() as u64);
    }

    #[test]
    fn state_snapshot_round_trips_all_storage_classes() {
        let mut core = SailCore::new().unwrap();
        let original = core.state().unwrap();
        let mut state = original.clone();
        state.registers[3] = 0x1111_2222_3333_4444;
        state.floating_registers[5] = 0x5555_6666_7777_8888;
        state.vector_registers[7][11] = 0xa5;
        state.predicate_registers[9][1] = 0x5a;
        state.sp = 0x1000;
        state.pc = 0x2000;
        state.lpc = 0x3000;
        state.lpa = 0x8000_0000_0000_0042;
        state.lkl = 0x0123_4567_89ab_cdef;
        state.lkh = 0xfedc_ba98_7654_3210;
        state.flags = 0x0d;
        state.status = 0x101;
        state.segments[2] = 0x1234_5000;
        state.controls.base_bootcfg = 0xfeed_face_cafe_beef;
        state.fstatus = 0x123;
        state.fflags = 0x1f;
        state.vstatus = 3;
        let expected = state.clone();

        assert_eq!(core.set_state(state), SailCoreStatus::Ok);
        assert_eq!(core.state(), Ok(expected));
        assert_eq!(core.set_state(original.clone()), SailCoreStatus::Ok);
        assert_eq!(core.state(), Ok(original));
    }

    #[test]
    fn advertised_fp_state_record_is_available_on_a_new_core() {
        let directory = super::platform::cpuid_query(0x0000_0001_0000_0001);
        let fp_features = super::platform::cpuid_query(0x0000_0001_0001_0001);
        assert_ne!(directory & 1, 0);
        assert_eq!((fp_features >> 8) & 0xff, 1);

        let mut core = SailCore::new().unwrap();
        assert_eq!(core.state().unwrap().fp_enabled, 1);
        assert_eq!(core.set_register(0, 0x1000), SailCoreStatus::Ok);
        assert_eq!(core.execute(&FSAVE_R0), SailCoreStatus::NeedsEnvironment);
    }

    #[test]
    fn state_record_saves_emit_their_owner_record() {
        let mut base = SailCore::new().unwrap();
        let mut state = base.state().unwrap();
        state.registers[0] = 0x1000;
        state.registers[1] = 0x1122_3344_5566_7788;
        state.flags = 0x0d;
        state.lpc = 0x1234_5678_9abc_def0;
        state.lpa = 0x8000_0000_0000_0042;
        state.floating_registers[0] = 0xfeed_face_cafe_beef;
        assert_eq!(base.set_state(state), SailCoreStatus::Ok);
        assert_eq!(base.execute(&SAVE_R0), SailCoreStatus::NeedsEnvironment);
        let request = base.last_request().unwrap();
        assert_eq!(request.kind, request_kind::WRITE);
        assert_eq!(request.width, 200);
        assert_eq!(request.range_length, 200);
        assert_eq!(request.payload.len(), 200);
        assert_eq!(word(&request.payload, 0), 0x1000);
        assert_eq!(word(&request.payload, 8), 0x1122_3344_5566_7788);
        assert_eq!(word(&request.payload, 176), 0x0d);
        assert_eq!(word(&request.payload, 184), 0x1234_5678_9abc_def0);
        assert_eq!(word(&request.payload, 192), 0x8000_0000_0000_0042);

        let mut supervisor = SailCore::new().unwrap();
        let mut state = supervisor.state().unwrap();
        state.registers[0] = 0x1000;
        state.status = 0x10;
        state.supervisor = 1;
        assert_eq!(supervisor.set_state(state), SailCoreStatus::Ok);
        assert_eq!(
            supervisor.execute(&SSAVE_R0),
            SailCoreStatus::NeedsEnvironment
        );
        let request = supervisor.last_request().unwrap();
        assert_eq!(request.kind, request_kind::WRITE);
        assert_eq!(request.width, 8);
        assert_eq!(request.payload, 0x10_u64.to_le_bytes());

        let mut fp = SailCore::new().unwrap();
        let mut state = fp.state().unwrap();
        state.registers[0] = 0x1000;
        state.fp_state_modified = 1;
        state.floating_registers[0] = 0x8877_6655_4433_2211;
        state.fflags = 0x1f;
        state.fstatus = 0x123;
        assert_eq!(fp.set_state(state), SailCoreStatus::Ok);
        assert_eq!(fp.execute(&FSAVE_R0), SailCoreStatus::NeedsEnvironment);
        let request = fp.last_request().unwrap();
        assert_eq!(request.kind, request_kind::WRITE);
        assert_eq!(request.width, 192);
        assert_eq!(word(&request.payload, 0), 0x0003_ffff);
        assert_eq!(word(&request.payload, 8), 0x0000_0000_0123_001f);
        assert_eq!(word(&request.payload, 16), 0x8877_6655_4433_2211);
        assert_eq!(fp.state().unwrap().fp_state_modified, 1);

        let mut vector = SailCore::new().unwrap();
        let mut state = vector.state().unwrap();
        state.registers[0] = 0x1000;
        state.vector_state_modified = 1;
        state.vstatus = 3;
        state.vector_registers[0][0] = 0xa5;
        state.predicate_registers[0][0] = 0x5a;
        assert_eq!(vector.set_state(state), SailCoreStatus::Ok);
        assert_eq!(vector.execute(&VSAVE_R0), SailCoreStatus::NeedsEnvironment);
        let request = vector.last_request().unwrap();
        assert_eq!(request.kind, request_kind::WRITE);
        assert_eq!(request.width, 640);
        assert_eq!(request.payload.len(), 640);
        assert_eq!(word(&request.payload, 0), 0x0001_ffff_ffff_ffff);
        assert_eq!(word(&request.payload, 8), 3_u64 << 16);
        assert_eq!(request.payload[64], 0xa5);
        assert_eq!(request.payload[576], 0x5a);
        assert_eq!(vector.state().unwrap().vector_state_modified, 1);
    }

    #[test]
    fn base_user_restore_changes_only_base_user_state() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.registers[0] = 0x1000;
        before.floating_registers[2] = 0xaaaa_bbbb_cccc_dddd;
        before.vector_registers[3][4] = 0x5a;
        before.fp_state_modified = 1;
        before.vector_state_modified = 1;
        before.lkl = 0x1111_2222_3333_4444;
        before.lkh = 0x5555_6666_7777_8888;
        assert_eq!(core.set_state(before.clone()), SailCoreStatus::Ok);

        let mut record = vec![0_u8; 200];
        for index in 0..16 {
            record[index * 8..index * 8 + 8]
                .copy_from_slice(&(0x100_u64 + index as u64).to_le_bytes());
        }
        for index in 0..6 {
            record[128 + index * 8..136 + index * 8]
                .copy_from_slice(&before.segments[3 + index].to_le_bytes());
        }
        record[176..184].copy_from_slice(&0x0b_u64.to_le_bytes());
        record[184..192].copy_from_slice(&0x1234_5678_9abc_def0_u64.to_le_bytes());
        record[192..200].copy_from_slice(&0x8000_0000_0000_002a_u64.to_le_bytes());

        assert_eq!(core.execute(&RESTORE_R0), SailCoreStatus::NeedsEnvironment);
        assert_eq!(core.last_request().unwrap().range_length, 200);
        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: record,
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Ok
        );
        let after = core.state().unwrap();
        assert_eq!(after.registers[0], 0x100);
        assert_eq!(after.registers[15], 0x10f);
        assert_eq!(after.flags, 0x0b);
        assert_eq!(after.lpc, 0x1234_5678_9abc_def0);
        assert_eq!(after.lpa, 0x8000_0000_0000_002a);
        assert_eq!(after.lkl, before.lkl);
        assert_eq!(after.lkh, before.lkh);
        assert_eq!(after.status, before.status);
        assert_eq!(after.current_dfa, before.current_dfa);
        assert_eq!(after.floating_registers, before.floating_registers);
        assert_eq!(after.vector_registers, before.vector_registers);
        assert_eq!(after.fp_state_modified, 1);
        assert_eq!(after.vector_state_modified, 1);
    }

    #[test]
    fn fp_clear_bitmap_omits_payload_reads_and_installs_initial_state() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.registers[0] = 0x1000;
        before.fp_state_modified = 1;
        before.floating_registers[5] = 0xfeed_face_cafe_beef;
        before.fflags = 0x1f;
        before.fstatus = 0x123;
        before.vector_registers[2][3] = 0xa5;
        assert_eq!(core.set_state(before.clone()), SailCoreStatus::Ok);

        assert_eq!(core.execute(&FRESTORE_R0), SailCoreStatus::NeedsEnvironment);
        let bitmap_request = core.last_request().unwrap();
        assert_eq!(bitmap_request.range_length, 192);
        assert_eq!(bitmap_request.memory_ranges.len(), 1);
        assert_eq!(bitmap_request.memory_ranges[0].effective_address, 0x1000);
        assert_eq!(bitmap_request.memory_ranges[0].buffer_offset, 0);
        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: vec![0; 8],
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::NeedsEnvironment
        );
        let body_request = core.last_request().unwrap();
        assert_eq!(body_request.range_length, 192);
        assert_eq!(body_request.body_length, 192);
        assert_eq!(body_request.memory_ranges.len(), 3);
        assert_eq!(body_request.memory_ranges[0].effective_address, 0x1000);
        assert_eq!(body_request.memory_ranges[0].width, 8);
        assert_eq!(body_request.memory_ranges[1].effective_address, 0x100c);
        assert_eq!(body_request.memory_ranges[1].width, 4);
        assert_eq!(body_request.memory_ranges[2].effective_address, 0x1090);
        assert_eq!(body_request.memory_ranges[2].width, 48);
        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: vec![0; 192],
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Ok
        );
        let after = core.state().unwrap();
        assert!(after.floating_registers.iter().all(|value| *value == 0));
        assert_eq!(after.fflags, 0);
        assert_eq!(after.fstatus, 0);
        assert_eq!(after.fp_state_modified, 0);
        assert_eq!(after.vector_registers, before.vector_registers);
    }

    #[test]
    fn supervisor_state_record_forms_are_rejected_in_user_mode() {
        for instruction in [
            &SSAVE_R0[..],
            &SRESTORE_R0[..],
            &CFISSAVE_R0[..],
            &CFISRESTORE_R0[..],
        ] {
            let mut core = SailCore::new().unwrap();
            let before = core.state().unwrap();
            assert_eq!(core.execute(instruction), SailCoreStatus::Fault);
            assert_eq!(core.state(), Ok(before));
        }
    }

    #[test]
    fn supervisor_restore_rejects_an_invalid_live_user_return_bank() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.registers[0] = 0x1000;
        before.status = 0x10;
        before.supervisor = 1;
        before.controls.base_uctl = 1 << 32;
        before.controls.base_ucs = 0x35;
        assert_eq!(core.set_state(before.clone()), SailCoreStatus::Ok);
        let saved_status = 0x451_u64;

        assert_eq!(core.execute(&SRESTORE_R0), SailCoreStatus::NeedsEnvironment);
        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: saved_status.to_le_bytes().to_vec(),
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Fault
        );
        assert_eq!(core.state(), Ok(before));
    }

    #[test]
    fn invalid_base_user_record_leaves_state_unchanged() {
        let mut core = SailCore::new().unwrap();
        let mut before = core.state().unwrap();
        before.registers[0] = 0x1000;
        before.registers[1] = 0x1234;
        assert_eq!(core.set_state(before.clone()), SailCoreStatus::Ok);
        let mut record = vec![0_u8; 200];
        record[128..136].copy_from_slice(&u64::MAX.to_le_bytes());

        assert_eq!(core.execute(&RESTORE_R0), SailCoreStatus::NeedsEnvironment);
        assert_eq!(
            core.resume(SailCoreResponse {
                kind: response_kind::READ,
                success: true,
                body: record,
                known: true,
                present: true,
                ..SailCoreResponse::default()
            }),
            SailCoreStatus::Fault
        );
        assert_eq!(core.state(), Ok(before));
    }

    #[test]
    fn state_restore_is_rejected_while_execution_is_pending() {
        let mut core = SailCore::new().unwrap();
        assert_eq!(core.set_sp(0x1000), SailCoreStatus::Ok);
        let state = core.state().unwrap();
        assert_eq!(core.execute(&[0x30]), SailCoreStatus::NeedsEnvironment);
        assert_eq!(core.set_state(state), SailCoreStatus::BadState);
        assert_eq!(core.cancel(), SailCoreStatus::Ok);
    }

    #[test]
    fn scalar_and_vector_fp_execute_without_numeric_requests() {
        let mut scalar = SailCore::new().unwrap();
        let mut scalar_state = scalar.state().unwrap();
        scalar_state.floating_registers[0] = u64::from(1.0_f32.to_bits());
        scalar_state.floating_registers[1] = u64::from(2.0_f32.to_bits());
        assert_eq!(scalar.set_state(scalar_state), SailCoreStatus::Ok);
        assert_eq!(scalar.execute(&[0xc2, 0x82, 0x10]), SailCoreStatus::Ok);
        assert_eq!(
            scalar.state().unwrap().floating_registers[0],
            u64::from(3.0_f32.to_bits())
        );

        let mut vector = SailCore::new().unwrap();
        let mut vector_state = vector.state().unwrap();
        vector_state.predicate_registers[0] = [0xff, 0xff];
        for lane in 0..4 {
            vector_state.vector_registers[0][lane * 4..lane * 4 + 4]
                .copy_from_slice(&1.0_f32.to_bits().to_le_bytes());
            vector_state.vector_registers[1][lane * 4..lane * 4 + 4]
                .copy_from_slice(&2.0_f32.to_bits().to_le_bytes());
        }
        assert_eq!(vector.set_state(vector_state), SailCoreStatus::Ok);
        // VFADD.S P0, V1, V0
        assert_eq!(
            vector.execute(&[0xcb, 0xf7, 0x83, 0x00, 0x20]),
            SailCoreStatus::Ok
        );
        for lane in 0..4 {
            assert_eq!(
                &vector.state().unwrap().vector_registers[0][lane * 4..lane * 4 + 4],
                &3.0_f32.to_bits().to_le_bytes()
            );
        }
    }
}
