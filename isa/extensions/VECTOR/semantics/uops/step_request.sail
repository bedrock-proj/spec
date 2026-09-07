function vector_step_operation(operation : Semantic_operation) -> bool = {
    let memory_kind = vector_semantics(operation).memory;
    memory_kind == Some(VectorGather) | memory_kind == Some(VectorScatter)
}

function vector_lane_bytes_loop(image : list(bits(8)), index : int, remaining : int) -> list(bits(8)) =
    if remaining <= 0 then {
        [||]
    } else {
        vector_byte_at(image, index) :: vector_lane_bytes_loop(image, index + 1, remaining - 1)
    }

function vector_lane_bytes(image : list(bits(8)), lane : int, element_bytes : int) -> list(bits(8)) =
    vector_lane_bytes_loop(image, lane * element_bytes, element_bytes)

function vector_step_register_index(instruction : Decoded_instruction, name : Operand_id) -> option(bits(4)) =
    match operand_named(instruction, name) {
        Some(operand) => Some(operand.value[3 .. 0]),
        None() => None(),
    }

function vector_step_predicate_overlap_conflict(instruction : Decoded_instruction) -> bool =
    match (
        predicate_register_index(instruction, Operand_govern),
        predicate_register_index(instruction, Operand_completion),
    ) {
        (Some(govern), Some(completion)) => govern == completion,
        _ => false,
    }

function vector_step_gather_address_overlap_conflict(instruction : Decoded_instruction) -> bool =
    if vector_semantics(instruction.form.operation).memory != Some(VectorGather) then {
        false
    } else {
        match (
            vector_register_index(instruction, Operand_address),
            vector_register_index(instruction, Operand_dst),
        ) {
            (Some(address), Some(destination)) => address == destination,
            _ => false,
        }
    }

function vector_step_normalize_completion(
    govern : list(bits(8)),
    completion : list(bits(8)),
    image : list(bits(8)),
    lane : int,
    lanes : int,
    element_bytes : int,
) -> (
    list(bits(8))
) =
    if lane >= lanes then {
        image
    } else {
        let bit_index = lane * element_bytes;
        vector_step_normalize_completion(
            govern,
            completion,
            predicate_set_bit(
                image,
                bit_index,
                predicate_bit(govern, bit_index) & predicate_bit(completion, bit_index),
            ),
            lane + 1,
            lanes,
            element_bytes,
        )
    }

function vector_step_first_pending(
    govern : list(bits(8)),
    completion : list(bits(8)),
    lane : int,
    lanes : int,
    element_bytes : int,
) -> (
    option(int)
) =
    if lane >= lanes then {
        None()
    } else {
        let bit_index = lane * element_bytes;
        if predicate_bit(govern, bit_index) & not_bool(predicate_bit(completion, bit_index)) then {
            Some(lane)
        } else {
            vector_step_first_pending(govern, completion, lane + 1, lanes, element_bytes)
        }
    }

function vector_step_payload(instruction : Decoded_instruction, name : Operand_id, exact : bool) -> int =
    match operand_named(instruction, name) {
        None() => 0,
        Some(operand) => if exact then {
            unsigned(immediate_value(operand))
        } else {
            signed(immediate_value(operand))
        },
    }

function vector_step_address(
    instruction : Decoded_instruction,
    state : Cpu_state,
    lane : int,
    element_bytes : int,
) -> (
    option(bits(64))
) = {
    let form_id = instruction.form.form_id;
    if    form_id
       == Form_xxlong_vgather_b_w_l_q_z_pn_p_pn_c_rn_b_add_rn_s_vn_v
       |  form_id
       == Form_xxlong_vscatter_b_w_l_q_z_pn_p_pn_c_vn_v_rn_b_add_rn_s then {
        match (
            vector_step_register_index(instruction, Operand_base),
            vector_step_register_index(instruction, Operand_stride),
        ) {
            (Some(base), Some(stride)) => Some(
                get_slice_int(
                    64,
                    unsigned(state.registers[unsigned(base)]) + lane * signed(state.registers[unsigned(stride)]),
                    0,
                ),
            ),
            _ => None(),
        }
    } else {
        match vector_register_index(instruction, Operand_address) {
            None() => None(),
            Some(address) => {
                let lane_value = vector_lane_unsigned(state.vector_registers[unsigned(address)], lane, element_bytes);
                if    form_id == Form_xxlong_vgather_l_pn_p_pn_c_vn_x_vn_v
                   |  form_id == Form_xxlong_vgather_q_pn_p_pn_c_vn_x_vn_v
                   |  form_id == Form_xxlong_vscatter_l_pn_p_pn_c_vn_v_vn_x
                   |  form_id == Form_xxlong_vscatter_q_pn_p_pn_c_vn_v_vn_x then {
                    Some(get_slice_int(64, lane_value, 0))
                } else {
                    match vector_step_register_index(instruction, Operand_base) {
                        None() => None(),
                        Some(base) => {
                            let lane_offset = if    form_id
                                                 == Form_xxlong_vgather_b_w_l_q_z_pn_p_pn_c_rn_b_add_vn_x_vn_v
                                                 |  form_id
                                                 == Form_xxlong_vscatter_b_w_l_q_z_pn_p_pn_c_vn_v_rn_b_add_vn_x then {
                                    vector_signed_lane(lane_value, element_bytes)
                                } else {
                                    lane_value * element_bytes
                                };
                            let displacement = if    form_id
                                                  == Form_xxlong_vgather_b_w_l_q_z_pn_p_pn_c_rn_b_add_vn_x_mul_scale_add_disp8s_vn_v
                                                  |  form_id
                                                  == Form_xxlong_vscatter_b_w_l_q_z_pn_p_pn_c_vn_v_rn_b_add_vn_x_mul_scale_add_disp8s then {
                                    vector_step_payload(instruction, Operand_disp8s, false)
                                } else if    form_id
                                          == Form_xxlong_vgather_b_w_l_q_z_pn_p_pn_c_rn_b_add_vn_x_mul_scale_add_disp16s_vn_v
                                          |  form_id
                                          == Form_xxlong_vscatter_b_w_l_q_z_pn_p_pn_c_vn_v_rn_b_add_vn_x_mul_scale_add_disp16s then {
                                    vector_step_payload(instruction, Operand_disp16s, false)
                                } else if    form_id
                                          == Form_xxlong_vgather_b_w_l_q_z_pn_p_pn_c_rn_b_add_vn_x_mul_scale_add_disp32s_vn_v
                                          |  form_id
                                          == Form_xxlong_vscatter_b_w_l_q_z_pn_p_pn_c_vn_v_rn_b_add_vn_x_mul_scale_add_disp32s then {
                                    vector_step_payload(instruction, Operand_disp32s, false)
                                } else if    form_id
                                          == Form_xxlong_vgather_b_w_l_q_z_pn_p_pn_c_rn_b_add_vn_x_mul_scale_add_disp64_vn_v
                                          |  form_id
                                          == Form_xxlong_vscatter_b_w_l_q_z_pn_p_pn_c_vn_v_rn_b_add_vn_x_mul_scale_add_disp64 then {
                                    vector_step_payload(instruction, Operand_disp64, true)
                                } else {
                                    0
                                };
                            Some(
                                get_slice_int(
                                    64,
                                    unsigned(state.registers[unsigned(base)]) + lane_offset + displacement,
                                    0,
                                ),
                            )
                        },
                    }
                }
            },
        }
    }
}

function vector_step_request(
    instruction : Decoded_instruction,
    state : Cpu_state,
    address : bits(64),
    element_bytes : int,
    values : list(bits(8)),
) -> (
    Primitive_request
) = {
    let operation = instruction.form.operation;
    let gather = vector_semantics(operation).memory == Some(VectorGather);
    let segment_image = state.segments[1];
    let point = segment_point(segment_image, address);
    let total = unsigned(address) + element_bytes;
    struct {
        kind = if gather then Read else Write,
        ordinal = 0,
        operand = vector_memory_operand_ordinal(instruction),
        role = RequestRoleValue,
        domain = RequestDomainCurrent,
        segment = 1,
        segment_image = segment_image,
        width = element_bytes,
        range_start = address,
        range_end = add_bits(address, get_slice_int(64, element_bytes, 0)),
        range_length = element_bytes,
        range_wrap = total > 18446744073709551616,
        range_end_at_modulus = total == 18446744073709551616,
        effective_address = address,
        linear_address = point.linear,
        access = if gather then AccessLoad else AccessStore,
        value = 0x0000000000000000,
        address_translation = true,
        debug_validation = false,
        debug_validated = false,
        physical_address = 0x0000000000000000,
        memory_ranges = [|struct {
            effective_address = address,
            linear_address = point.linear,
            width = element_bytes,
            buffer_offset = 0,
        }|],
        validation_ranges = [|struct {
            effective_address = address,
            linear_address = point.linear,
            width = element_bytes,
        }|],
        read_completion = ReadCompletionData,
        memory_cache_hint = MemoryCacheDefault,
        commit_point = true,
        memory_order = 0,
        cache_policy = 0,
        suppress_fault = false,
        staged_updates = [||],
        selector = if gather then 0x0000000000000000 else 0x0000000000000001,
        expected = 0x0000000000000000,
        desired = 0x0000000000000000,
        auxiliary = get_slice_int(64, element_bytes, 0),
        body_length = if gather then element_bytes else 0,
        payload_bytes = if gather then [||] else values,
    }
}
