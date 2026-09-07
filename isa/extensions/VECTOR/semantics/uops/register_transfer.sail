function vector_special_vclr_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let active_bytes = vector_current_length_bytes(state);
    match vector_register_index(instruction, Operand_dst) {
        Some(destination) => {
            var updated = state;
            updated.vector_registers[unsigned(destination)] = vector_zero_low_bytes(
                state.vector_registers[unsigned(destination)], active_bytes);
            Some(VectorComputeState(updated))
        },
        None() => None(),
    }
}

function vector_special_vdup_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let fp_form : bool = match condition_size_field(instruction.fields) {
        Some(selector) => selector >= 5,
        None() => false,
    };
    if not_bool(fp_form) then {
        match (
            value_named(instruction, state, Operand_src),
            value_named(instruction, state, Operand_immediate),
            vector_register_index(instruction, Operand_dst),
        ) {
            (Some(source), _, Some(destination)) => {
                let image = vector_duplicate_image(
                    vector_zero_low_bytes(state.vector_registers[unsigned(destination)], active_bytes),
                    0, lanes, element_bytes, unsigned(source));
                var updated = state;
                updated.vector_registers[unsigned(destination)] = image;
                Some(VectorComputeState(updated))
            },
            (_, Some(source), Some(destination)) => {
                let image = vector_duplicate_image(
                    vector_zero_low_bytes(state.vector_registers[unsigned(destination)], active_bytes),
                    0, lanes, element_bytes, unsigned(source));
                var updated = state;
                updated.vector_registers[unsigned(destination)] = image;
                Some(VectorComputeState(updated))
            },
            _ => None(),
        }
    } else {
        None()
    }
}

function vector_special_vextract_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
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
            value_named(instruction, state, Operand_index),
            operand_named(instruction, Operand_dst),
        ) {
            (Some(source), Some(index_value), Some(destination)) => {
                let index = unsigned(index_value);
                if index >= lanes then {
                    Some(VectorComputeFault(
                        faulted_with_error_code(
                            state,
                            operation,
                            VectorRangeFault,
                            "VEXTRACT lane index exceeds the lane count",
                            0x0000000000000001,
                        ),
                    ))
                } else {
                    var updated = state;
                    updated.registers[unsigned(destination.value[3 .. 0])] = get_slice_int(
                        64,
                        vector_lane_unsigned(state.vector_registers[unsigned(source)], index, element_bytes),
                        0,
                    );
                    Some(VectorComputeState(updated))
                }
            },
            _ => None(),
        }
    } else {
        None()
    }
}

function vector_special_vindex_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match vector_register_index(instruction, Operand_dst) {
        Some(destination) => {
            var updated = state;
            updated.vector_registers[unsigned(destination)] = vector_index_image(
                vector_zero_low_bytes(state.vector_registers[unsigned(destination)], active_bytes),
                0,
                lanes,
                element_bytes,
            );
            Some(VectorComputeState(updated))
        },
        None() => None(),
    }
}

function vector_special_vinsert_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
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
            value_named(instruction, state, Operand_src),
            value_named(instruction, state, Operand_index),
            vector_register_index(instruction, Operand_dst),
        ) {
            (Some(source), Some(index_value), Some(destination)) => {
                let index = unsigned(index_value);
                if index >= lanes then {
                    Some(VectorComputeFault(
                        faulted_with_error_code(
                            state,
                            operation,
                            VectorRangeFault,
                            "VINSERT lane index exceeds the lane count",
                            0x0000000000000001,
                        ),
                    ))
                } else {
                    var updated = state;
                    updated.vector_registers[unsigned(destination)] = vector_write_lane(
                        state.vector_registers[unsigned(destination)],
                        index,
                        element_bytes,
                        unsigned(source),
                    );
                    Some(VectorComputeState(updated))
                }
            },
            _ => None(),
        }
    } else {
        None()
    }
}

function vector_special_vlcadd_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (operand_named(instruction, Operand_offset), operand_named(instruction, Operand_dst)) {
        (Some(offset), Some(dst)) => {
            let index = unsigned(dst.value[3 .. 0]);
            var updated = state;
            updated.registers[index] = add_bits(
                state.registers[index],
                get_slice_int(64, signed(immediate_value(offset)) * lanes, 0),
            );
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_vlcnt_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match operand_named(instruction, Operand_dst) {
        Some(dst) => {
            var updated = state;
            updated.registers[unsigned(dst.value[3 .. 0])] = get_slice_int(64, lanes, 0);
            Some(VectorComputeState(updated))
        },
        None()    => None(),
    }
}

function vector_special_vmov_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (vector_register_index(instruction, Operand_src), vector_register_index(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            var updated = state;
            updated.vector_registers[unsigned(destination)] = match predicate_register_index(
                instruction,
                Operand_govern,
            ) {
                Some(predicate) => vector_merge_image(
                    state.vector_registers[unsigned(destination)],
                    state.vector_registers[unsigned(source)],
                    state.predicate_registers[unsigned(predicate)],
                    0,
                    lanes,
                    element_bytes,
                ),
                None() => vector_replace_low_bytes(
                    state.vector_registers[unsigned(destination)],
                    state.vector_registers[unsigned(source)],
                    active_bytes),
            };
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}
