function uop_stack_request_make(
    kind : Primitive_request_kind,
    state : Cpu_state,
    address : bits(64),
    range_end : bits(64),
    access : Transaction_access,
    value : bits(64),
    commit_point : bool,
) -> (
    Primitive_request
) = {
    let point = segment_point(state.segments[2], address);
    struct {
        kind = kind,
        ordinal = 0,
        operand = 255,
        role = RequestRoleStack,
        domain = RequestDomainStack,
        segment = 2,
        segment_image = state.segments[2],
        width = 8,
        range_start = address,
        range_end = range_end,
        range_length = unsigned(sub_bits(range_end, address)),
        range_wrap = unsigned(range_end) < unsigned(address) & range_end != 0x0000000000000000,
        range_end_at_modulus = range_end == 0x0000000000000000 & address != 0x0000000000000000,
        effective_address = address,
        linear_address = point.linear,
        access = access,
        value = if kind == Write then 0x0000000000000000 else value,
        address_translation = true,
        debug_validation = false,
        debug_validated = false,
        physical_address = 0x0000000000000000,
        memory_ranges = [|struct {
            effective_address = address,
            linear_address = point.linear,
            width = unsigned(sub_bits(range_end, address)),
            buffer_offset = 0,
        }|],
        validation_ranges = [|struct {
            effective_address = address,
            linear_address = point.linear,
            width = unsigned(sub_bits(range_end, address)),
        }|],
        read_completion = ReadCompletionData,
        memory_cache_hint = MemoryCacheDefault,
        commit_point = commit_point,
        memory_order = 0,
        cache_policy = 0,
        suppress_fault = false,
        staged_updates = [||],
        selector = 0x0000000000000000,
        expected = 0x0000000000000000,
        desired = 0x0000000000000000,
        auxiliary = 0x0000000000000000,
        body_length = if kind == Read then unsigned(sub_bits(range_end, address)) else 0,
        payload_bytes = if kind == Write
                        then memory_value_bytes(value, unsigned(sub_bits(range_end, address)))
                        else [||],
    }
}

union clause Uop_kind = UopStackRange : bool

union clause Uop_kind = UopStackAdjust : bool

function uop_stack_adjust_value(current : bits(64), push : bool,
                                width : int) -> option(bits(64)) =
    if push then
        if current != 0x0000000000000000 & unsigned(current) < width then None()
        else Some(sub_bits(current, bits_from_int(width)))
    else if unsigned(current) > width_modulus(8) - width then None()
    else Some(add_bits(current, bits_from_int(width)))

function uop_stack_adjust_error_code(push : bool, width : int) -> bits(64) = {
    let access = if push then 2 else 1;
    let size = if width == 1 then 1
               else if width == 2 then 2
               else if width == 4 then 3
               else if width == 8 then 4
               else 0;
      0x0000000000000004
    | sail_shiftleft(bits_from_int(access), 8)
    | sail_shiftleft(bits_from_int(size), 27)
}

function clause execute_uop(UopStackAdjust(push), uop, values, instruction,
                            before, speculative) =
    match uop_location_read(uop.source0, speculative, values) {
        Some(current) => match uop_stack_adjust_value(
            current, push, uop.width
        ) {
            None() => uop_step_fault(faulted_with_error_code(
                before, instruction.form.operation, BoundsFault,
                "stack pointer adjustment is outside the 64-bit address space",
                uop_stack_adjust_error_code(push, uop.width)
            )),
            Some(next_value) => match uop_location_write(
                uop.destination, next_value, 64,
                instruction.form.operation, speculative, values
            ) {
                Some((updated, next_values)) =>
                    uop_step_continue(updated, next_values),
                None() => uop_step_fault(faulted(
                    before, instruction.form.operation, InvalidControlState,
                    "stack pointer adjustment destination is unavailable"
                )),
            },
        },
        None() => uop_step_fault(faulted(
            before, instruction.form.operation, InvalidControlState,
            "stack pointer adjustment source is unavailable"
        )),
    }

function clause execute_uop(UopStackRange(push), uop, values, instruction, before, speculative) =
    match uop_location_read(uop.source0, speculative, values) {
        Some(start) => {
            let end_address = add_bits(start, get_slice_int(64, uop.width, 0));
            uop_step_wait(
                0 - 1,
                uop_stack_request_make(
                    StackRange,
                    speculative,
                    start,
                    end_address,
                    if push then AccessStackWrite else AccessStackRead,
                    0x0000000000000000,
                    false,
                ),
                0x0000000000000000,
            )
        },
        None() => uop_step_fault(
            faulted(before, instruction.form.operation, InvalidControlState, "uop stack range address is unavailable"),
        ),
    }

union clause Uop_kind = UopStackLoad : int

function clause execute_uop(UopStackLoad(offset), uop, values, instruction, before, speculative) =
    match uop_location_read(uop.source0, speculative, values) {
        Some(base) => {
            let address = add_bits(base, get_slice_int(64, offset, 0));
            uop_step_wait(
                uop.destination.index,
                uop_stack_request_make(
                    Read,
                    speculative,
                    address,
                    add_bits(address, 0x0000000000000008),
                    AccessStackRead,
                    0x0000000000000000,
                    false,
                ),
                0x0000000000000000,
            )
        },
        None() => uop_step_fault(
            faulted(before, instruction.form.operation, InvalidControlState, "uop stack load address is unavailable"),
        ),
    }

union clause Uop_kind = UopStackStorePair : unit
union clause Uop_kind = UopStackStore : unit

function clause execute_uop(UopStackStore(), uop, values, instruction, before, speculative) =
    match (uop_location_read(uop.source0, speculative, values),
           uop_location_read(uop.source1, speculative, values)) {
        (Some(value), Some(address)) => uop_step_wait(0 - 1,
            uop_stack_request_make(Write, speculative, address,
                add_bits(address, 0x0000000000000008), AccessStackWrite, value, true), value),
        _ => uop_step_fault(faulted(before, instruction.form.operation,
            InvalidControlState, "uop stack store input is unavailable")),
    }

function clause execute_uop(UopStackStorePair(), uop, values, instruction, before, speculative) =
    match (
        uop_location_read(uop.source0, speculative, values),
        uop_location_read(uop.source1, speculative, values),
        uop_location_read(uop.destination, speculative, values),
    ) {
        (Some(first), Some(second), Some(address)) => {
            let point = segment_point(speculative.segments[2], address);
            let request = {
                uop_stack_request_make(
                    Write,
                    speculative,
                    address,
                    add_bits(address, 0x0000000000000010),
                    AccessStackWrite,
                    second,
                    true,
                ) with
                linear_address = point.linear,
                width = 16,
                range_length = 16,
                payload_bytes = append_bytes(
                    memory_value_bytes(second, 8), memory_value_bytes(first, 8)),
            };
            uop_step_wait(0 - 1, request, second)
        },
        _ => uop_step_fault(
            faulted(
                before,
                instruction.form.operation,
                InvalidControlState,
                "uop stack pair store input is unavailable",
            ),
        ),
    }
