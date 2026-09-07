function vector_special_pcount_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (predicate_register_index(instruction, Operand_predicate), operand_named(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            let result = predicate_count(state.predicate_registers[unsigned(source)], 0, lanes, element_bytes, 0);
            var updated = state;
            updated.registers[unsigned(destination.value[3 .. 0])] =
                if result < 0 then {
                    0xFFFFFFFFFFFFFFFF
                } else {
                    get_slice_int(64, result, 0)
                };
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_pfalse_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match predicate_register_index(instruction, Operand_dst) {
        None() => None(),
        Some(destination) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = predicate_fill_typed(
                vector_zero_low_bytes(
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                0,
                lanes,
                element_bytes,
                0,
                0,
            );
            Some(VectorComputeState(updated))
        },
    }
}

function vector_special_pfirst_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (predicate_register_index(instruction, Operand_predicate), operand_named(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            let result = predicate_first(state.predicate_registers[unsigned(source)], 0, lanes, element_bytes);
            var updated = state;
            updated.registers[unsigned(destination.value[3 .. 0])] =
                if result < 0 then {
                    0xFFFFFFFFFFFFFFFF
                } else {
                    get_slice_int(64, result, 0)
                };
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_phead_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match predicate_register_index(instruction, Operand_dst) {
        None() => None(),
        Some(destination) => {
            let boundary : int = match value_named(instruction, state, Operand_count) {
                Some(value) => tmod_int(unsigned(value), lanes),
                None() => 0,
            };
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = predicate_fill_typed(
                vector_zero_low_bytes(
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                0,
                lanes,
                element_bytes,
                boundary,
                lanes,
            );
            Some(VectorComputeState(updated))
        },
    }
}

function vector_special_plast_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (predicate_register_index(instruction, Operand_predicate), operand_named(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            let result = predicate_last(state.predicate_registers[unsigned(source)], lanes - 1, element_bytes);
            var updated = state;
            updated.registers[unsigned(destination.value[3 .. 0])] =
                if result < 0 then {
                    0xFFFFFFFFFFFFFFFF
                } else {
                    get_slice_int(64, result, 0)
                };
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_pnot_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    match predicate_register_index(instruction, Operand_dst) {
        Some(destination) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = vector_replace_low_bytes(
                state.predicate_registers[unsigned(destination)],
                predicate_raw_not(
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                active_predicate_bytes);
            Some(VectorComputeState(updated))
        },
        None() => None(),
    }
}

function vector_special_ppackhi_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (predicate_register_index(instruction, Operand_src), predicate_register_index(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            let source_image = state.predicate_registers[unsigned(source)];
            let image = predicate_pack_image(source_image,
                vector_zero_low_bytes(state.predicate_registers[unsigned(destination)],
                                      active_predicate_bytes),
                0, lanes, element_bytes, true);
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = image;
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_ppacklo_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (predicate_register_index(instruction, Operand_src), predicate_register_index(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            let source_image = state.predicate_registers[unsigned(source)];
            let image = predicate_pack_image(source_image,
                vector_zero_low_bytes(state.predicate_registers[unsigned(destination)],
                                      active_predicate_bytes),
                0, lanes, element_bytes, false);
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = image;
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_pperm_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (vector_register_index(instruction, Operand_index), predicate_register_index(instruction, Operand_dst)) {
        (Some(indices), Some(destination)) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = predicate_permute_image(
                state.predicate_registers[unsigned(destination)],
                state.vector_registers[unsigned(indices)],
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

function vector_special_psel_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    match (
        predicate_register_index(instruction, Operand_select),
        predicate_register_index(instruction, Operand_src),
        predicate_register_index(instruction, Operand_dst),
    ) {
        (Some(select), Some(source), Some(destination)) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = vector_replace_low_bytes(
                state.predicate_registers[unsigned(destination)],
                predicate_raw_select(
                    state.predicate_registers[unsigned(select)],
                    state.predicate_registers[unsigned(source)],
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                active_predicate_bytes);
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_ptail_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match predicate_register_index(instruction, Operand_dst) {
        None() => None(),
        Some(destination) => {
            let boundary : int = match value_named(instruction, state, Operand_count) {
                Some(value) => tmod_int(unsigned(value), lanes),
                None() => 0,
            };
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = predicate_fill_typed(
                vector_zero_low_bytes(
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                0,
                lanes,
                element_bytes,
                0,
                boundary,
            );
            Some(VectorComputeState(updated))
        },
    }
}

function vector_special_ptrue_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match predicate_register_index(instruction, Operand_dst) {
        None() => None(),
        Some(destination) => {
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = predicate_fill_typed(
                vector_zero_low_bytes(
                    state.predicate_registers[unsigned(destination)],
                    active_predicate_bytes,
                ),
                0,
                lanes,
                element_bytes,
                0,
                lanes,
            );
            Some(VectorComputeState(updated))
        },
    }
}

function vector_special_punpkhi_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (predicate_register_index(instruction, Operand_src), predicate_register_index(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            let source_image = state.predicate_registers[unsigned(source)];
            let image = predicate_unpack_image(source_image,
                vector_zero_low_bytes(state.predicate_registers[unsigned(destination)],
                                      active_predicate_bytes),
                0, lanes, element_bytes, true);
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = image;
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}

function vector_special_punpklo_compute(instruction : Decoded_instruction, state : Cpu_state) -> option(Vector_compute_result) = {
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let active_predicate_bytes = tdiv_int(active_bytes, 8);
    let lanes = tdiv_int(active_bytes, element_bytes);
    match (predicate_register_index(instruction, Operand_src), predicate_register_index(instruction, Operand_dst)) {
        (Some(source), Some(destination)) => {
            let source_image = state.predicate_registers[unsigned(source)];
            let image = predicate_unpack_image(source_image,
                vector_zero_low_bytes(state.predicate_registers[unsigned(destination)],
                                      active_predicate_bytes),
                0, lanes, element_bytes, false);
            var updated = state;
            updated.predicate_registers[unsigned(destination)] = image;
            Some(VectorComputeState(updated))
        },
        _ => None(),
    }
}
