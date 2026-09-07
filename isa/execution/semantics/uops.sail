function uop_location_make_plain(
    kind : Uop_location_kind, index : int, immediate : bits(64)
) -> Uop_location = struct {
    kind = kind,
    index = index,
    immediate = immediate,
    update_difference = None(),
    negative = false,
}

function uop_location_make_none() -> Uop_location =
    uop_location_make_plain(UopLocationNone, 0, 0x0000000000000000)

function uop_location_make_register(index : int) -> Uop_location =
    uop_location_make_plain(UopLocationRegister, index, 0x0000000000000000)

function uop_location_make_fp_register(index : int) -> Uop_location =
    uop_location_make_plain(UopLocationFpRegister, index, 0x0000000000000000)

function uop_location_make_sp() -> Uop_location =
    uop_location_make_plain(UopLocationSp, 0, 0x0000000000000000)

function uop_location_make_segment(index : int) -> Uop_location =
    uop_location_make_plain(UopLocationSegment, index, 0x0000000000000000)

function uop_segment_write_state(state : Cpu_state, index : int, value : bits(64)) -> Cpu_state = {
    var updated = state;
    if index == 0 then updated.segments[0] = value
    else if index == 1 then updated.segments[1] = value
    else if index == 2 then updated.segments[2] = value
    else if index == 3 then updated.segments[3] = value
    else if index == 4 then updated.segments[4] = value
    else if index == 5 then updated.segments[5] = value
    else if index == 6 then updated.segments[6] = value
    else if index == 7 then updated.segments[7] = value
    else updated.segments[8] = value;
    updated
}

function uop_location_make_immediate(value : bits(64)) -> Uop_location =
    uop_location_make_plain(UopLocationImmediate, 0, value)

function uop_location_make_temporary(index : int) -> Uop_location =
    uop_location_make_plain(UopLocationTemporary, index, 0x0000000000000000)

function memory_bytes_value(
    bytes : list(bits(8)), offset : int, count : int
) -> bits(64) =
    if count <= 0 then 0x0000000000000000
    else match bytes {
        [||] => 0x0000000000000000,
        byte :: tail => if offset > 0 then memory_bytes_value(tail, offset - 1, count)
                        else sail_zero_extend(byte, 64)
                           | sail_shiftleft(memory_bytes_value(tail, 0, count - 1), 8),
    }

function uop_location_make_ea_update_amount(
    difference : option(Ea_update_difference), width : int, negative : bool
) -> Uop_location = match difference {
    Some(selected) => struct {
        kind = UopLocationEaUpdateAmount,
        index = width,
        immediate = 0x0000000000000000,
        update_difference = Some(selected),
        negative = negative,
    },
    None() => uop_location_make_none(),
}

function uop_value_empty() -> Uop_value = struct {
    value = 0x0000000000000000,
    secondary_value = 0x0000000000000000,
    flags = 0x0,
    write_flags = false,
    generated_fflags = 0b00000,
    bounds_passed = false,
    cache_policy = 0,
    access_class = NormalAccess,
    physical_class = NormalPhysical,
    atomic_store_happened = false,
    body_bytes = [||],
    known = false,
    present = false,
    address = false,
    segment = 0,
    segment_image = 0x0000000000000000,
    effective_address = 0x0000000000000000,
    linear_address = 0x0000000000000000,
    domain = RequestDomainCurrent,
}

function uop_value_get(values : list(Uop_value), index : int) -> option(Uop_value) =
    match values {
        [||] => None(),
        value :: tail => if index <= 0 then {
            Some(value)
        } else {
            uop_value_get(tail, index - 1)
        },
    }

function uop_value_set(values : list(Uop_value), index : int, value : Uop_value) -> list(Uop_value) =
    if index < 0 then {
        values
    } else {
        match values {
            [||] => if index == 0 then {
                [|value|]
            } else {
                uop_value_empty() :: uop_value_set([||], index - 1, value)
            },
            head :: tail => if index == 0 then {
                value :: tail
            } else {
                head :: uop_value_set(tail, index - 1, value)
            },
        }
    }

function uop_location_read(location : Uop_location, state : Cpu_state, values : list(Uop_value)) -> option(bits(64)) =
    if location.kind == UopLocationRegister then {
        Some(state.registers[unsigned(get_slice_int(4, location.index, 0))])
    } else {
        if location.kind == UopLocationFpRegister then {
            Some(state.floating_registers[unsigned(get_slice_int(4, location.index, 0))])
        } else {
            if location.kind == UopLocationSp then {
                Some(state.sp)
            } else {
                if location.kind == UopLocationSegment then {
                    Some(image_segment(state, location.index))
                } else if location.kind == UopLocationImmediate then {
                    Some(location.immediate)
                } else if location.kind == UopLocationEaUpdateAmount then {
                    match location.update_difference {
                        Some(difference) => {
                            let amount = ea_update_amount(difference, state, location.index);
                            Some(bits_from_int(if location.negative then 0 - amount else amount))
                        },
                        None() => None(),
                    }
                } else {
                    if location.kind == UopLocationTemporary then {
                        match uop_value_get(values, location.index) {
                            Some(value) => Some(value.value),
                            None() => None(),
                        }
                    } else {
                        None()
                    }
                }
            }
        }
    }

function uop_location_write(
    location : Uop_location,
    value : bits(64),
    width : int,
    operation : Semantic_operation,
    state : Cpu_state,
    values : list(Uop_value),
) -> (
    option((Cpu_state, list(Uop_value)))
) = {
    var updated = state;
    if location.kind == UopLocationRegister then {
        updated.registers[unsigned(get_slice_int(4, location.index, 0))] = canonical_register_result(
            operation,
            value,
            width,
        );
        Some((updated, values))
    } else {
        if location.kind == UopLocationSp then {
            updated.sp = value;
            Some((updated, values))
        } else if location.kind == UopLocationSegment then {
            if segment_image_valid(value) then {
                Some((uop_segment_write_state(updated, location.index, value), values))
            } else None()
        } else {
            if location.kind == UopLocationTemporary then {
                Some((updated, uop_value_set(values, location.index, { uop_value_empty() with value = value })))
            } else {
                None()
            }
        }
    }
}

function uop_program_get(program : list(Uop), cursor : int) -> option(Uop) =
    match program {
        [||] => None(),
        operation :: tail => if cursor <= 0 then {
            Some(operation)
        } else {
            uop_program_get(tail, cursor - 1)
        },
    }

function uop_memory_request_make(
    kind : Primitive_request_kind,
    address : Uop_value,
    width : int,
    access : Transaction_access,
    value : bits(64),
    commit_point : bool,
) -> (
    Primitive_request
) =
    struct {
        kind = kind,
        ordinal = 0,
        operand = 255,
        role = RequestRoleValue,
        domain = address.domain,
        segment = address.segment,
        segment_image = address.segment_image,
        width = width,
        range_start = address.effective_address,
        range_end = add_bits(address.effective_address, get_slice_int(64, width, 0)),
        range_length = width,
        range_wrap = unsigned(address.effective_address) + width > 18446744073709551616,
        range_end_at_modulus = unsigned(address.effective_address) + width == 18446744073709551616,
        effective_address = address.effective_address,
        linear_address = address.linear_address,
        access = access,
        value = if kind == Write then 0x0000000000000000 else value,
        address_translation = true,
        debug_validation = false,
        debug_validated = false,
        physical_address = 0x0000000000000000,
        memory_ranges = [|struct {
            effective_address = address.effective_address,
            linear_address = address.linear_address,
            width = width,
            buffer_offset = 0,
        }|],
        validation_ranges = [|struct {
            effective_address = address.effective_address,
            linear_address = address.linear_address,
            width = width,
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
        body_length = if kind == Read then width else 0,
        payload_bytes = if kind == Write then memory_value_bytes(value, width) else [||],
    }

function uop_program_make_waiting_result(
    program : list(Uop),
    cursor : int,
    values : list(Uop_value),
    pending_destination : int,
    instruction : Decoded_instruction,
    before : Cpu_state,
    speculative : Cpu_state,
    request : Primitive_request,
    captured : bits(64),
    commit_state : bool,
) -> (
    Execution_result
) =
    struct {
        state = if commit_state then speculative else before,
        pending =
            {
                empty_pending(Some(speculative), instruction.form.operation, CommitMemory) with
                instruction = Some(instruction),
                continuation = PendingUop(struct {
                    program = program,
                    cursor = cursor,
                    values = values,
                    pending_destination = pending_destination,
                    captured = captured,
                }),
            },
        request = request,
        effects = [||],
        fault = None(),
        debug_stop = None(),
        awaiting_environment = true,
    }

val uop_program_run : (list(Uop), int, list(Uop_value), Decoded_instruction, Cpu_state, Cpu_state) -> Execution_result

val execute_uop : (Uop_kind, Uop, list(Uop_value), Decoded_instruction, Cpu_state, Cpu_state) -> Uop_step

scattered function execute_uop

function uop_step_continue(state : Cpu_state, values : list(Uop_value)) -> Uop_step =
    UopContinue(struct { state = state, values = values })

function uop_step_wait(pending_destination : int, request : Primitive_request, captured : bits(64)) -> Uop_step =
    UopWait(struct {
        pending_destination = pending_destination,
        request = request,
        captured = captured,
        commit_state = false,
    })

function uop_step_wait_committed(
    pending_destination : int,
    request : Primitive_request,
    captured : bits(64),
) -> (
    Uop_step
) =
    UopWait(struct {
        pending_destination = pending_destination,
        request = request,
        captured = captured,
        commit_state = true,
    })

function uop_step_complete(state : Cpu_state, commit_kind : Commit_kind) -> Uop_step =
    UopComplete(struct {
        state = state,
        commit_kind = commit_kind,
        effects = [||],
        advance_pc = true,
        boundary_class = BoundaryOrdinary,
    })

function uop_step_complete_control(state : Cpu_state, commit_kind : Commit_kind) -> Uop_step =
    UopComplete(struct {
        state = state,
        commit_kind = commit_kind,
        effects = [||],
        advance_pc = false,
        boundary_class = BoundaryOrdinary,
    })

function uop_step_complete_boundary(
    state : Cpu_state,
    commit_kind : Commit_kind,
    boundary_class : Boundary_class,
) -> (
    Uop_step
) =
    UopComplete(struct {
        state = state,
        commit_kind = commit_kind,
        effects = [||],
        advance_pc = false,
        boundary_class = boundary_class,
    })

function uop_step_complete_with_effects(state : Cpu_state, commit_kind : Commit_kind,
                                   effects : list(Architectural_effect)) -> Uop_step =
    UopComplete(struct {
        state = state,
        commit_kind = commit_kind,
        effects = effects,
        advance_pc = true,
        boundary_class = BoundaryOrdinary,
    })

function uop_step_fault(result : Execution_result) -> Uop_step = UopFault(result)

function uop_step_delegate(result : Execution_result) -> Uop_step = UopDelegate(result)

function uop_step_interpret(
    step : Uop_step,
    program : list(Uop),
    cursor : int,
    values : list(Uop_value),
    instruction : Decoded_instruction,
    before : Cpu_state,
    speculative : Cpu_state,
) -> (
    Execution_result
) =
    match step {
        UopContinue(next) => uop_program_run(program, cursor + 1, next.values, instruction, before, next.state),
        UopWait(wait) => {
            let request : Primitive_request = match uop_program_get(program, cursor) {
                Some(uop) => if wait.request.operand != 255
                                | (wait.request.role != RequestRoleValue
                                   & wait.request.role != RequestRoleAddress) then wait.request
                             else { wait.request with operand = operand_ordinal(
                                 instruction, uop.operand_name) },
                None() => wait.request,
            };
            uop_program_make_waiting_result(
                program,
                cursor + 1,
                values,
                wait.pending_destination,
                instruction,
                before,
                speculative,
                debug_trigger_prepare_request(
                    if wait.commit_state then speculative else before,
                    request,
                ),
                wait.captured,
                wait.commit_state,
            )
        },
        UopComplete(done) => {
            let result = completed(
                before,
                if done.advance_pc then advance_pc(done.state, instruction) else done.state,
                done.commit_kind,
                done.effects,
            );
            { result with pending = { result.pending with boundary_class = done.boundary_class } }
        },
        UopDelegate(result) => result,
        UopFault(result) => result,
    }

function uop_program_run(
    program : list(Uop),
    cursor : int,
    values : list(Uop_value),
    instruction : Decoded_instruction,
    before : Cpu_state,
    speculative : Cpu_state,
) -> (
    Execution_result
) =
    match uop_program_get(program, cursor) {
        None() => faulted(before, instruction.form.operation, InvalidControlState, "uop program ended without commit"),
        Some(uop) => uop_step_interpret(
            execute_uop(uop.kind, uop, values, instruction, before, speculative),
            program,
            cursor,
            values,
            instruction,
            before,
            speculative,
        ),
    }

function uop_program_execute(
    program : list(Uop),
    instruction : Decoded_instruction,
    state : Cpu_state,
) -> (
    Execution_result
) =
    uop_program_run(program, 0, [||], instruction, state, state)

function uop_program_preserve_parent(next : Execution_result, previous : Execution_result) -> Execution_result =
    if not_bool(next.awaiting_environment) then {
        next
    } else {
        {
            next with
            pending =
                {
                    next.pending with
                    repeat_parent = previous.pending.repeat_parent,
                    boundary_class = previous.pending.boundary_class,
                },
        }
    }

function uop_program_resume(previous : Execution_result, response : Transaction_response) -> Execution_result =
    if previous.request.kind == Read
       & byte_count(response.body_bytes) != previous.request.body_length then
        faulted(previous.state, previous.pending.operation, InvalidControlState,
                "memory read response length mismatch")
    else match (previous.pending.instruction, uop_continuation_get(previous.pending)) {
        (Some(instruction), Some(continuation)) => {
            let response_value = if previous.request.kind == Read
                                 then memory_bytes_value(response.body_bytes, 0, 8)
                                 else response.value;
            let response_secondary_value = if previous.request.kind == Read
                                           then memory_bytes_value(response.body_bytes, 8, 8)
                                           else response.secondary_value;
            let values = if continuation.pending_destination >= 0 then {
                    uop_value_set(
                        continuation.values,
                        continuation.pending_destination,
                        struct {
                            value = response_value,
                            secondary_value = response_secondary_value,
                            flags = response.flags,
                            write_flags = response.write_flags,
                            generated_fflags = response.generated_fflags,
                            bounds_passed = response.bounds_passed,
                            cache_policy = response.cache_policy,
                            access_class = response.access_class,
                            physical_class = response.physical_class,
                            atomic_store_happened = response.atomic_store_happened,
                            body_bytes = response.body_bytes,
                            known = response.known,
                            present = response.present,
                            address = false,
                            segment = 0,
                            segment_image = 0x0000000000000000,
                            effective_address = 0x0000000000000000,
                            linear_address = 0x0000000000000000,
                            domain = RequestDomainCurrent,
                        },
                    )
                } else {
                    continuation.values
                };
            uop_program_preserve_parent(
                uop_program_run(
                    continuation.program,
                    continuation.cursor,
                    values,
                    instruction,
                    previous.state,
                    pending_after(previous),
                ),
                previous,
            )
        },
        _ => faulted(
            previous.state,
            previous.pending.operation,
            InvalidControlState,
            "uop continuation is incomplete",
        ),
    }
