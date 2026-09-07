function vector_fp_conversion_destination_bytes(operation : Semantic_operation) -> int =
    vector_semantics(operation).fp_conversion_destination_bytes

function vector_fp_conversion_destination_fp(operation : Semantic_operation) -> bool =
    vector_semantics(operation).fp_conversion_destination_fp

function vector_fp_conversion_source_kind(
    instruction : Decoded_instruction,
    source_bytes : int,
    conversion : Fp_conversion_operation,
) -> (
    bits(8)
) = {
    let operation = instruction.form.operation;
    if vector_fp_conversion_destination_fp(operation) & conversion == FpConvertUnsigned then {
        0x05
    } else {
        if vector_fp_conversion_destination_fp(operation) then {
            match size_codes(instruction.form.sizes) {
                Size_L :: _ => 0x04,
                Size_Q :: _ => 0x04,
                _ => vector_fp_value_kind(source_bytes),
            }
        } else {
            vector_fp_value_kind(source_bytes)
        }
    }
}

function vector_fp_conversion_lanes(
    instruction : Decoded_instruction,
    source : list(bits(8)),
    destination : list(bits(8)),
    predicate : list(bits(8)),
    offset : int,
    vector_bytes : int,
    source_bytes : int,
    destination_bytes : int,
    control : bits(8),
    result : list(bits(8)),
    generated_causes : bits(5),
) -> (
    option(Vector_fp_image)
) =
    if offset >= vector_bytes then {
        Some(struct { bytes = result, generated_causes = generated_causes })
    } else {
        let container_bytes = if source_bytes > destination_bytes then { source_bytes } else { destination_bytes };
        if not_bool(predicate_bit(predicate, offset)) then {
            vector_fp_conversion_lanes(
                instruction,
                source,
                destination,
                predicate,
                offset + container_bytes,
                vector_bytes,
                source_bytes,
                destination_bytes,
                control,
                result,
                generated_causes,
            )
        } else {
            let conversion = if vector_semantics(instruction.form.operation).fp_conversion_unsigned then {
                    FpConvertUnsigned
                } else {
                    FpConvertSigned
                };
            match fp_primitive_evaluate(
                instruction.form.operation,
                source_bytes,
                if vector_fp_conversion_destination_fp(instruction.form.operation)
                then vector_fp_result_kind(destination_bytes)
                else 0x04,
                control,
                1,
                vector_fp_conversion_source_kind(instruction, source_bytes, conversion),
                get_slice_int(64, vector_lane_unsigned_loop(source, offset, source_bytes, 1, 0), 0),
                0x00,
                0x0000000000000000,
                0x00,
                0x0000000000000000,
                false,
            ) {
                None() => None(),
                Some(evaluation) => vector_fp_conversion_lanes(
                    instruction,
                    source,
                    destination,
                    predicate,
                    offset + container_bytes,
                    vector_bytes,
                    source_bytes,
                    destination_bytes,
                    control,
                    vector_write_lane(
                        result,
                        tdiv_int(offset, destination_bytes),
                        destination_bytes,
                        unsigned(evaluation.primary),
                    ),
                    generated_causes | evaluation.generated_causes,
                ),
            }
        }
    }

function vector_fp_named_image(
    instruction : Decoded_instruction,
    state : Cpu_state,
    memory : list(bits(8)),
    name : Operand_id,
) -> (
    option(list(bits(8)))
) =
    match operand_named(instruction, name) {
        Some(operand) => if operand.operand_type == OperandType_Vn then {
            Some(state.vector_registers[unsigned(operand.value[4 .. 0])])
        } else {
            if operand.operand_type == OperandType_VEA then {
                Some(memory)
            } else {
                None()
            }
        },
        None() => None(),
    }

function vector_fp_evaluate_image(
    instruction : Decoded_instruction,
    state : Cpu_state,
    memory : list(bits(8)),
) -> (
    option(Vector_fp_image)
) = {
    let operation = instruction.form.operation;
    let element_bytes = operation_width(instruction);
    let active_bytes = vector_current_length_bytes(state);
    let lanes = tdiv_int(active_bytes, element_bytes);
    let predicate = vector_memory_predicate(instruction, state);
    let empty_vector = zero_vector_bytes(active_bytes);
    let control = fp_primitive_control(
        fp_fstatus_rounding_mode(state.fstatus),
        fp_fstatus_ftz(state.fstatus),
        fp_fstatus_daz(state.fstatus),
        fp_fstatus_dn(state.fstatus),
    );
    let compare_right : option(list(bits(8))) = match vector_fp_named_image(instruction, state, memory, Operand_rhs) {
        Some(image) => Some(image),
        None() => vector_fp_named_image(instruction, state, memory, Operand_src),
    };
    if vector_semantics(operation).fp_compare then {
        match (vector_fp_named_image(instruction, state, memory, Operand_lhs), compare_right) {
            (Some(left), Some(right)) => {
                let condition : int = match operand_named(instruction, Operand_cc) {
                    Some(value) => unsigned(value.value[3 .. 0]),
                    None() => 0,
                };
                match vector_fp_compare_lanes(
                    left,
                    right,
                    predicate,
                    0,
                    lanes,
                    element_bytes,
                    control,
                    condition,
                    zero_vector_bytes(tdiv_int(active_bytes, 8)),
                    fflags_none,
                ) {
                    Some(image) => Some(image),
                    None() => None(),
                }
            },
            _ => None(),
        }
    } else {
        if vector_fp_convert_operation(operation) then {
            match (
                vector_fp_named_image(instruction, state, memory, Operand_src),
                vector_fp_named_image(instruction, state, memory, Operand_dst),
            ) {
                (Some(source), Some(destination)) => match vector_fp_conversion_lanes(
                    instruction,
                    source,
                    destination,
                    predicate,
                    0,
                    active_bytes,
                    element_bytes,
                    vector_fp_conversion_destination_bytes(operation),
                    control,
                    destination,
                    fflags_none,
                ) {
                    Some(image) => Some(image),
                    None() => None(),
                },
                _ => None(),
            }
        } else {
            if vector_fp_primitive_operation(operation) | vector_fp_exact_operation(operation) then {
                match vector_fp_named_image(instruction, state, memory, Operand_dst) {
                    None() => None(),
                    Some(destination) => {
                        let source_image = vector_fp_named_image(instruction, state, memory, Operand_src);
                        let source : list(bits(8)) = match source_image {
                            Some(image) => image,
                            None() => empty_vector,
                        };
                        let lhs : list(bits(8)) = match vector_fp_named_image(instruction, state, memory, Operand_lhs) {
                            Some(image) => image,
                            None() => empty_vector,
                        };
                        let rhs : list(bits(8)) = match vector_fp_named_image(instruction, state, memory, Operand_rhs) {
                            Some(image) => image,
                            None() => empty_vector,
                        };
                        match vector_fp_arithmetic_lanes(
                            operation,
                            source,
                            lhs,
                            rhs,
                            destination,
                            predicate,
                            0,
                            lanes,
                            element_bytes,
                            control,
                            destination,
                            fflags_none,
                            source_image != None(),
                        ) {
                            Some(image) => Some(image),
                            None() => None(),
                        }
                    },
                }
            } else {
                None()
            }
        }
    }
}
