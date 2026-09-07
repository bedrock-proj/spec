function vector_predicate_binary_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    match (predicate_register_index(instruction, Operand_src), predicate_register_index(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = vector_replace_low_bytes(
                state.predicate_registers[unsigned(destination)],
                predicate_raw_binary(
                    state.predicate_registers[unsigned(destination)],
                    state.predicate_registers[unsigned(source)],
                    operation,
                    active_predicate_bytes,
                ),
                active_predicate_bytes);
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_predicate_slide_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let empty = zero_vector_bytes(active_predicate_bytes);
    match (predicate_register_index(instruction, Operand_dst), operand_named(instruction, Operand_count)) {
        (Some(destination), Some(count_operand)) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = predicate_slide_image(
                operation,
                state.predicate_registers[unsigned(destination)],
                vector_zero_low_bytes(
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                empty,
                0,
                lanes,
                element_bytes,
                unsigned(immediate_value(count_operand)),
            );
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_predicate_pair_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (
        predicate_register_index(instruction, Operand_lhs),
        predicate_register_index(instruction, Operand_rhs),
        predicate_register_index(instruction, Operand_dst),
    ) {
        (Some(left), Some(right), Some(destination)) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = predicate_pair_image(
                operation,
                state.predicate_registers[unsigned(left)],
                state.predicate_registers[unsigned(right)],
                vector_zero_low_bytes(
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                0,
                lanes,
                element_bytes,
            );
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}
