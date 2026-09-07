// Shared register-only vector compute families. Instruction files own operation metadata and entries.

function vector_integer_binary_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let fp_form : bool = match condition_size_field(instruction.fields) {
        Some(selector) => selector >= 5,
        None() => false,
    };
    if not_bool(fp_form) then {
        match (
            vector_register_index(instruction, Operand_src),
            vector_register_index(instruction, Operand_dst),
            predicate_register_index(instruction, Operand_govern),
        ) {
            (Some(source), Some(destination), Some(predicate)) => {
                var updated = state;
                updated.vector_registers[unsigned(destination)] = vector_integer_binary_image(
                    operation,
                    state.vector_registers[unsigned(destination)],
                    state.vector_registers[unsigned(source)],
                    state.predicate_registers[unsigned(predicate)],
                    0,
                    lanes,
                    element_bytes,
                );
                Some(VectorComputeState(updated))
            },
            _ => None(),
        }
    } else {
        None()
    }
}

function vector_integer_unary_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let fp_form : bool = match condition_size_field(instruction.fields) {
        Some(selector) => selector >= 5,
        None() => false,
    };
    if not_bool(fp_form) then {
        match (vector_register_index(instruction, Operand_dst), predicate_register_index(instruction, Operand_govern)) {
            (Some(destination), Some(predicate)) => {
                var updated = state;
                updated.vector_registers[unsigned(destination)] = vector_integer_unary_image(
                    operation,
                    state.vector_registers[unsigned(destination)],
                    state.predicate_registers[unsigned(predicate)],
                    0,
                    lanes,
                    element_bytes,
                );
                Some(VectorComputeState(updated))
            },
            _ => None(),
        }
    } else {
        None()
    }
}

function vector_integer_reduction_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let lanes = tdiv_int(vector_current_length_bytes(state), element_bytes);
    match (
        vector_register_index(instruction, Operand_src),
        predicate_register_index(instruction, Operand_govern),
        operand_named(instruction, Operand_dst),
    ) {
        (Some(source), Some(predicate), Some(destination)) => {
            var updated = state;
            updated.registers[unsigned(destination.value[3 .. 0])] = get_slice_int(
                64,
                vector_integer_reduction_image(
                    operation,
                    state.vector_registers[unsigned(source)],
                    state.predicate_registers[unsigned(predicate)],
                    0,
                    lanes,
                    element_bytes,
                    vector_integer_reduction_identity(operation, element_bytes),
                ),
                0,
            );
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_shift_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let zero_vector = zero_vector_bytes(active_bytes);
    match (vector_register_index(instruction, Operand_dst), predicate_register_index(instruction, Operand_govern)) {
        (Some(destination), Some(predicate)) => {
            let count_vector = vector_register_index(instruction, Operand_src);
            let counts : list(bits(8)) = match count_vector {
                Some(index) => state.vector_registers[unsigned(index)],
                None() => zero_vector,
            };
            let scalar_count : int = match operand_named(instruction, Operand_count) {
                Some(count_operand) => unsigned(immediate_value(count_operand)),
                None() => 0,
            };
            var updated = state;
            updated.vector_registers[unsigned(destination)] = vector_shift_image(
                operation,
                state.vector_registers[unsigned(destination)],
                counts,
                scalar_count,
                count_vector != None(),
                state.predicate_registers[unsigned(predicate)],
                0,
                lanes,
                element_bytes,
            );
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_integer_compare_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let fp_form : bool = match condition_size_field(instruction.fields) {
        Some(selector) => selector >= 5,
        None() => false,
    };
    if not_bool(fp_form) then {
        match (
            vector_register_index(instruction, Operand_lhs),
            vector_register_index(instruction, Operand_rhs),
            predicate_register_index(instruction, Operand_govern),
            predicate_register_index(instruction, Operand_dst),
        ) {
            (Some(left), Some(right), Some(govern), Some(destination)) => {
                let condition : int = match operand_named(instruction, Operand_cc) {
                    Some(value) => unsigned(value.value[3 .. 0]),
                    None() => 0,
                };
                var updated = state;
                updated.predicate_registers[unsigned(destination)] = vector_integer_compare_image(
                    operation,
                    condition,
                    state.vector_registers[unsigned(left)],
                    state.vector_registers[unsigned(right)],
                    state.predicate_registers[unsigned(govern)],
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
    } else {
        None()
    }
}

function vector_width_change_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    match (
        vector_register_index(instruction, Operand_src),
        vector_register_index(instruction, Operand_dst),
        predicate_register_index(instruction, Operand_govern),
    ) {
        (Some(source), Some(destination), Some(predicate)) => {
            let destination_bytes = vector_width_change_destination_bytes(operation);
            let container_bytes = if element_bytes > destination_bytes then {
                    element_bytes
                } else {
                    destination_bytes
                };
            var updated = state;
            updated.vector_registers[unsigned(destination)] = vector_width_change_image(
                operation,
                state.vector_registers[unsigned(destination)],
                state.vector_registers[unsigned(source)],
                state.predicate_registers[unsigned(predicate)],
                0,
                tdiv_int(active_bytes, container_bytes),
                element_bytes,
                destination_bytes,
            );
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_permute_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let zero_vector = zero_vector_bytes(active_bytes);
    match (vector_register_index(instruction, Operand_dst), predicate_register_index(instruction, Operand_govern)) {
        (Some(destination), Some(predicate)) => {
            let source : list(bits(8)) = match vector_register_index(instruction, Operand_src) {
                Some(index) => state.vector_registers[unsigned(index)],
                None() => zero_vector,
            };
            let count : int = match operand_named(instruction, Operand_count) {
                Some(count_operand) => unsigned(immediate_value(count_operand)),
                None() => 0,
            };
            let original = state.vector_registers[unsigned(destination)];
            var updated = state;
            updated.vector_registers[unsigned(destination)] = vector_permute_image(
                operation,
                original,
                source,
                original,
                state.predicate_registers[unsigned(predicate)],
                0,
                lanes,
                element_bytes,
                count,
            );
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}
