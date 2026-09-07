function vector_memory_operand(operands : list(Decoded_operand)) -> option(Decoded_operand) =
    match operands {
        [||] => None(),
        operand :: tail => match operand.ea {
            Some(_) => Some(operand),
            None() => vector_memory_operand(tail),
        },
    }

function vector_memory_request(
    kind : Primitive_request_kind,
    instruction : Decoded_instruction,
    state : Cpu_state,
    evaluation : Ea_evaluation,
    predicate : list(bits(8)),
    values : list(bits(8)),
    element_bytes : int,
    access : Transaction_access,
) -> (
    Primitive_request
) = {
    let predicate_move = vector_semantics(instruction.form.operation).memory == Some(VectorPredicateMove);
    let active_bytes = vector_current_length_bytes(state);
    let length = if predicate_move then {
            tdiv_int(active_bytes, 8)
        } else {
            active_bytes
        };
    let ranges = vector_memory_ranges(
            predicate_move, predicate, evaluation.effective_address,
            evaluation.linear_address, length, element_bytes);
    struct {
        kind = kind,
        ordinal = 0,
        operand = operand_ordinal(instruction, evaluation.operand_name),
        role = request_role_for_ea(evaluation.role),
        domain = request_domain_for_operand(evaluation.domain),
        segment = evaluation.segment,
        segment_image = evaluation.segment_image,
        width = element_bytes,
        range_start = evaluation.effective_address,
        range_end = add_bits(evaluation.effective_address, get_slice_int(64, length, 0)),
        range_length = length,
        range_wrap = unsigned(evaluation.effective_address) + length > 18446744073709551616,
        range_end_at_modulus = unsigned(evaluation.effective_address) + length == 18446744073709551616,
        effective_address = evaluation.effective_address,
        linear_address = evaluation.linear_address,
        access = access,
        value = 0x0000000000000000,
        address_translation = true,
        debug_validation = false,
        debug_validated = false,
        physical_address = 0x0000000000000000,
        memory_ranges = ranges,
        validation_ranges = memory_validation_ranges(ranges),
        read_completion = ReadCompletionData,
        memory_cache_hint = MemoryCacheDefault,
        commit_point = true,
        memory_order = 0,
        cache_policy = 0,
        suppress_fault = false,
        staged_updates = evaluation.staged_updates,
        selector = 0x0000000000000000,
        expected = 0x0000000000000000,
        desired = 0x0000000000000000,
        auxiliary = get_slice_int(64, element_bytes, 0),
        body_length = if kind == Read then length else 0,
        payload_bytes = if kind == Write then values else [||],
    }
}

union clause Uop_kind = UopVectorCalcEa : unit
union clause Uop_kind = UopVectorMemoryLoad : unit
union clause Uop_kind = UopVectorMemoryStore : unit
union clause Uop_kind = UopVectorMemoryCompute : Semantic_operation

val vector_integer_binary_operation : Semantic_operation -> bool
val vector_shift_operation_p : Semantic_operation -> bool
val vector_memory_direct_store_image : (Decoded_instruction, Cpu_state, list(bits(8)), int, int) -> option(
        list(bits(8)),
    )
val vector_response_length : (Decoded_instruction, Cpu_state) -> int
val vector_memory_source_result : (Decoded_instruction, Cpu_state, Cpu_state, list(bits(8))) -> option(Cpu_state)
val vector_memory_destination_result : (Decoded_instruction, Cpu_state, list(bits(8))) -> option(list(bits(8)))

function vector_uop_value_from_address(evaluation : Ea_evaluation) -> Uop_value =
    {
        uop_value_empty() with
        address = true,
        segment = evaluation.segment,
        segment_image = evaluation.segment_image,
        effective_address = evaluation.effective_address,
        linear_address = evaluation.linear_address,
        domain = request_domain_for_operand(evaluation.domain),
    }

function vector_uop_memory_request_make(
    kind : Primitive_request_kind,
    instruction : Decoded_instruction,
    state : Cpu_state,
    address : Uop_value,
    values : list(bits(8)),
    access : Transaction_access,
) -> (
    Primitive_request
) = {
    let predicate_move = vector_semantics(instruction.form.operation).memory == Some(VectorPredicateMove);
    let active_bytes = vector_current_length_bytes(state);
    let element_bytes = if predicate_move then {
            tdiv_int(active_bytes, 8)
        } else {
            operation_width(instruction)
        };
    let length = if predicate_move then {
            tdiv_int(active_bytes, 8)
        } else {
            active_bytes
        };
    let ranges = vector_memory_ranges(
            predicate_move, vector_memory_predicate(instruction, state),
            address.effective_address, address.linear_address, length, element_bytes);
    struct {
        kind = kind,
        ordinal = 0,
        operand = vector_memory_operand_ordinal(instruction),
        role = RequestRoleValue,
        domain = address.domain,
        segment = address.segment,
        segment_image = address.segment_image,
        width = element_bytes,
        range_start = address.effective_address,
        range_end = add_bits(address.effective_address, get_slice_int(64, length, 0)),
        range_length = length,
        range_wrap = unsigned(address.effective_address) + length > 18446744073709551616,
        range_end_at_modulus = unsigned(address.effective_address) + length == 18446744073709551616,
        effective_address = address.effective_address,
        linear_address = address.linear_address,
        access = access,
        value = 0x0000000000000000,
        address_translation = true,
        debug_validation = false,
        debug_validated = false,
        physical_address = 0x0000000000000000,
        memory_ranges = ranges,
        validation_ranges = memory_validation_ranges(ranges),
        read_completion = ReadCompletionData,
        memory_cache_hint = MemoryCacheDefault,
        commit_point = true,
        memory_order = 0,
        cache_policy = 0,
        suppress_fault = false,
        staged_updates = [||],
        selector = 0x0000000000000000,
        expected = 0x0000000000000000,
        desired = 0x0000000000000000,
        auxiliary = get_slice_int(64, element_bytes, 0),
        body_length = if kind == Read then length else 0,
        payload_bytes = if kind == Write then values else [||],
    }
}

function clause execute_uop(UopVectorCalcEa(), uop, values, instruction, before, speculative) =
    match vector_memory_operand(instruction.operands) {
        None() => uop_step_continue(speculative, values),
        Some(memory_operand) => {
            let evaluated = if vector_semantics(instruction.form.operation).memory == Some(VectorPredicateMove) then {
                    match memory_operand.ea {
                        None() => None(),
                        Some(ea) => Some(
                            evaluate_decoded_ea(
                                memory_operand,
                                ea,
                                speculative,
                                speculative.pc,
                                tdiv_int(vector_current_length_bytes(speculative), 8),
                            ),
                        ),
                    }
                } else {
                    evaluate_vector_instruction_ea(
                        instruction.operands,
                        speculative,
                        speculative.pc,
                        operation_width(instruction),
                    )
                };
            match evaluated {
                Some((evaluation, evaluated_state)) => uop_step_continue(
                    evaluated_state,
                    uop_value_set(values, uop.destination.index, vector_uop_value_from_address(evaluation)),
                ),
                None() => uop_step_fault(
                    faulted(
                        before,
                        instruction.form.operation,
                        InvalidControlState,
                        "missing vector uop memory address",
                    ),
                ),
            }
        },
    }

function clause execute_uop(UopVectorMemoryLoad(), uop, values, instruction, before, speculative) =
    match vector_memory_operand(instruction.operands) {
        None() => uop_step_continue(speculative, values),
        Some(memory_operand) => {
            let memory_destination = vector_operand_writes(memory_operand.name, instruction.operands);
            let needs_read =   not_bool(memory_destination)
                             | vector_fp_compute_operation(instruction.form.operation)
                             | vector_integer_binary_operation(instruction.form.operation)
                             | vector_shift_operation_p(instruction.form.operation);
            if not_bool(needs_read) then {
                uop_step_continue(speculative, values)
            } else {
                match uop_value_get(values, uop.source0.index) {
                    Some(address) => uop_step_wait(
                        uop.destination.index,
                        vector_uop_memory_request_make(
                            Read,
                            instruction,
                            speculative,
                            address,
                            [||],
                            if vector_operand_writes(memory_operand.name, instruction.operands)
                            then AccessReadModifyWrite
                            else AccessLoad,
                        ),
                        0x0000000000000000,
                    ),
                    None() => uop_step_fault(
                        faulted(
                            before,
                            instruction.form.operation,
                            InvalidControlState,
                            "vector uop load address is unavailable",
                        ),
                    ),
                }
            }
        },
    }

function clause execute_uop(UopVectorMemoryCompute(operation), uop, values, instruction, before, speculative) =
    if instruction.form.operation != operation then {
        uop_step_fault(
            faulted(
                before,
                instruction.form.operation,
                InvalidControlState,
                "vector memory uop does not match its instruction",
            ),
        )
    } else {
        match vector_memory_operand(instruction.operands) {
            None() => uop_step_fault(
                faulted(before, operation, InvalidControlState, "vector memory uop has no memory operand"),
            ),
            Some(memory_operand) => {
                let memory_destination = vector_operand_writes(memory_operand.name, instruction.operands);
                if memory_destination then {
                    let computed = if vector_integer_binary_operation(operation) | vector_shift_operation_p(operation) then {
                            match uop_value_get(values, uop.auxiliary.index) {
                                Some(memory) => vector_memory_destination_result(instruction, before, memory.body_bytes),
                                None() => None(),
                            }
                        } else {
                            let predicate_move = vector_semantics(operation).memory == Some(VectorPredicateMove);
                            let predicate = if predicate_move then {
                                    [||]
                                } else {
                                    vector_memory_predicate(instruction, before)
                                };
                            let element_bytes = if predicate_move then {
                                    tdiv_int(vector_current_length_bytes(before), 8)
                                } else {
                                    operation_width(instruction)
                                };
                            let lanes = if predicate_move then {
                                    1
                                } else {
                                    tdiv_int(vector_current_length_bytes(before), element_bytes)
                                };
                            vector_memory_direct_store_image(instruction, before, predicate, lanes, element_bytes)
                        };
                    match computed {
                        Some(output) => uop_step_continue(
                            speculative,
                            uop_value_set(values, uop.destination.index, { uop_value_empty() with body_bytes = output }),
                        ),
                        None() => uop_step_fault(
                            faulted(before, operation, IllegalInstruction, "unsupported vector memory destination uop"),
                        ),
                    }
                } else {
                    match uop_value_get(values, uop.auxiliary.index) {
                        Some(memory) => if byte_count(memory.body_bytes) != vector_response_length(instruction, before) then {
                            uop_step_fault(
                                faulted(
                                    before,
                                    operation,
                                    InvalidControlState,
                                    "vector memory response length mismatch",
                                ),
                            )
                        } else {
                            match vector_memory_source_result(instruction, before, speculative, memory.body_bytes) {
                                Some(updated) => uop_step_continue(updated, values),
                                None() => uop_step_fault(
                                    faulted(
                                        before,
                                        operation,
                                        IllegalInstruction,
                                        "unsupported vector memory source uop",
                                    ),
                                ),
                            }
                        },
                        None() => uop_step_fault(
                            faulted(before, operation, InvalidControlState, "vector memory response is unavailable"),
                        ),
                    }
                }
            },
        }
    }
