enum Repeat_observation_kind = {
    RepeatObserveCompareFlags,
    RepeatObserveFlags,
    RepeatObserveBoolean,
    RepeatObserveSource,
    RepeatObserveDestination,
}

val repeat_semantics : Semantic_operation -> Repeat_observation_kind

scattered function repeat_semantics

function byte_list_length(bytes : list(bits(8))) -> int = match bytes {
    [||] => 0,
    _ :: tail => 1 + byte_list_length(tail),
}

function repeat_body_available(instruction : Decoded_instruction, state : Cpu_state) -> bool =
    instruction_available(instruction, state)

function repeat_fetch_request(
    _prefix : Decoded_instruction,
    state : Cpu_state,
    body_start : bits(64),
) -> (
    Primitive_request
) =
    {
        empty_primitive_request() with
        kind = RepeatBodyFetch,
        role = RequestRoleSingle,
        domain = RequestDomainCode,
        segment = 0,
        segment_image = state.segments[0],
        effective_address = body_start,
        linear_address = segment_point(state.segments[0], body_start).linear,
        range_start = body_start,
        range_end = body_start,
        range_length = 0,
        access = AccessExecute,
        address_translation = true,
        range_wrap = false,
        range_end_at_modulus = false,
        body_length = 0,
    }

function start_repeat_transaction(instruction : Decoded_instruction, state : Cpu_state) -> Execution_result =
    match operand_named(instruction, Operand_counter) {
        None() => faulted(state, instruction.form.operation, IllegalInstruction, "missing repeat counter"),
        Some(counter) => {
            let index = counter.value[3 .. 0];
            let body_start = next_pc(state, instruction);
            let condition : int = match condition_field(instruction.fields) {
                Some(value) => value,
                None() => 0,
            };
            var staged = state;
            staged.repeat_state =
                struct {
                    active = true,
                    counter = index,
                    remaining = image_register(state, unsigned(index)),
                    condition = get_slice_int(4, condition, 0),
                    prefix_start = state.pc,
                    body_start = body_start,
                    fixed_body = [||],
                };
            let request = repeat_fetch_request(instruction, staged, body_start);
            waiting_transaction(TransactionRepeat,
                state,
                staged,
                instruction,
                CommitControl,
                empty_destination(),
                PhaseRepeatFetch,
                0,
                image_register(state, unsigned(index)),
                0x0000000000000000,
                request,
            )
        },
    }

val run_repeat_single : (Decoded_instruction, list(bits(8)), Cpu_state) -> Execution_result
val finish_repeat_single : (Decoded_instruction, list(bits(8)), Cpu_state, Cpu_state, bits(64)) -> Execution_result
function attach_repeat_parent(
    result : Execution_result,
    body : Decoded_instruction,
    all_bytes : list(bits(8)),
) -> (
    Execution_result
) =
    {
        result with
        pending =
            {
                result.pending with
                repeat_parent = Some(struct { body = body, bytes = all_bytes }),
                boundary_class = BoundaryRepeatIteration,
            },
    }

function repeat_observation_operand(wants_source : bool, operands : list(Decoded_operand)) -> option(Decoded_operand) =
    match operands {
        [||] => None(),
        operand :: tail => if wants_source & operand.access == AccessRead then {
            Some(operand)
        } else {
            if not_bool(wants_source) & (operand.access == AccessWrite | operand.access == AccessReadWrite) then {
                Some(operand)
            } else {
                repeat_observation_operand(wants_source, tail)
            }
        },
    }

function repeat_observation_with_capture(
    body : Decoded_instruction,
    before : Cpu_state,
    after : Cpu_state,
    captured : bits(64),
) -> (
    bits(4)
) = {
    let operation = body.form.operation;
    let semantics = repeat_semantics(operation);
    if semantics == RepeatObserveCompareFlags then {
        /* Reconstruct the body-derived value inside the same precise commit; this
           is not an architectural FLAGS observation boundary. */
        after.flags[3 .. 2] @ 0b00
    } else {
        if semantics == RepeatObserveFlags then {
            after.flags
        } else {
            if semantics == RepeatObserveBoolean then {
                flags_value(after.flags[0] == bitzero, false, false, false)
            } else {
                let source = semantics == RepeatObserveSource;
                match repeat_observation_operand(source, body.operands) {
                    None() => after.flags,
                    Some(operand) => {
                        let local = if source then {
                                local_operand_value(before, operand, instruction_ea_width(operand, operation_width(body)))
                            } else {
                                local_operand_value(after, operand, instruction_ea_width(operand, operation_width(body)))
                            };
                        let value : bits(64) = match local {
                            Some(item) => item,
                            None() => captured,
                        };
                        let width = operation_width(body);
                        flags_value(
                            narrow_bits(value, width) == 0x0000000000000000,
                            width_sign(value, width),
                            false,
                            false,
                        )
                    },
                }
            }
        }
    }
}

function finish_repeat_single(
    body : Decoded_instruction,
    bytes : list(bits(8)),
    before : Cpu_state,
    after : Cpu_state,
    captured : bits(64),
) -> (
    Execution_result
) = {
    let context = before.repeat_state;
    let remaining = sub_bits(context.remaining, 0x0000000000000001);
    let observed = repeat_observation_with_capture(body, before, after, captured);
    let continue = remaining != 0x0000000000000000 & condition_holds(unsigned(context.condition), observed);
    var updated = after;
    updated.registers[unsigned(context.counter)] = remaining;
    updated.repeat_state = { context with remaining = remaining,  fixed_body = bytes };
    if continue then {
        updated.pc = context.body_start;
        let done = completed(before, updated, CommitControl, [||]);
        { done with pending = { done.pending with boundary_class = BoundaryRepeatIteration } }
    } else {
        updated.repeat_state = { updated.repeat_state with active = false };
        updated.pc = add_bits(context.body_start, bits_from_int(body.encoded_length));
        let done = completed(before, updated, CommitControl, [||]);
        { done with pending = { done.pending with boundary_class = BoundaryRepeatIteration } }
    }
}

function run_repeat_single(body : Decoded_instruction, bytes : list(bits(8)), state : Cpu_state) -> Execution_result = {
    var iteration_state = state;
    iteration_state.pc = state.repeat_state.body_start;
    let result = execute_full(body, iteration_state);
    if result.awaiting_environment then {
        attach_repeat_parent(result, body, bytes)
    } else {
        if result.fault != None() | result.debug_stop != None() then {
            result
        } else {
            finish_repeat_single(body, bytes, iteration_state, result.state, pending_transaction(result.pending).captured_first)
        }
    }
}
