function fp_source_candidate(operand : Decoded_operand) -> bool =
    (operand.access == AccessRead | operand.access == AccessReadWrite) &
        (   operand.operand_type
        == OperandType_Fn
        |  operand.operand_type
        == OperandType_Rn
        |  operand.operand_type
        == OperandType_FEA
        |  operand.operand_type
        == OperandType_imm64)

function fp_source_at(operands : list(Decoded_operand), wanted : int, current : int) -> option(Decoded_operand) =
    match operands {
        [||] => None(),
        operand :: tail => if fp_source_candidate(operand) then {
            if current == wanted then {
                Some(operand)
            } else {
                fp_source_at(tail, wanted, current + 1)
            }
        } else {
            fp_source_at(tail, wanted, current)
        },
    }

function fp_destination_is_fn(instruction : Decoded_instruction) -> bool =
    match operand_named(instruction, Operand_dst) {
        Some(destination) => destination.operand_type == OperandType_Fn,
        None() => false,
    }

function fp_float_kind_for_width(width : int) -> Fp_value_kind =
    if width == 2 then FpBits16 else if width == 4 then FpBits32 else FpBits64

function fp_operand_bits_for_kind(kind : Fp_value_kind, raw : bits(64), width : int)
    -> bits(64) =
    if kind == FpBits16 then 0x000000000000 @ raw[15 .. 0]
    else if kind == FpBits32 then 0x00000000 @ raw[31 .. 0]
    else if kind == FpSigned64 then sign_extend_width(raw, width)
    else if kind == FpUnsigned64 then raw & width_mask(width)
    else raw

function fp_operand_kind_for(
    instruction : Decoded_instruction,
    operand : Decoded_operand,
    width : int,
) -> (
    Fp_value_kind
) = {
    let conversion_unsigned : option(bool) = match fp_semantics(instruction.form.operation) {
        Some(semantics) => semantics.conversion_unsigned,
        None() => None(),
    };
    if conversion_unsigned != None() then {
        if operand.operand_type == OperandType_Fn then {
            if fp_destination_is_fn(instruction) then fp_float_kind_for_width(width)
            else fp_float_kind_for_width(fp_conversion_fn_width(instruction.form.operation))
        } else if fp_destination_is_fn(instruction) then {
            if fp_size_list_is_integer(size_codes(instruction.form.sizes)) then
                if conversion_unsigned == Some(true) then FpUnsigned64 else FpSigned64
            else fp_float_kind_for_width(width)
        } else FpRaw64
    } else if operand.operand_type == OperandType_Fn then {
        fp_float_kind_for_width(width)
    } else {
        if operand.operand_type == OperandType_Rn then FpRaw64
        else if operand.operand_type == OperandType_FEA then match operand.ea {
            Some(ea) => if ea.kind == EaKind_immediate then {
                if ea_payload_width(EaPayloadRole_immediate, ea.payloads) == 32 then FpBits32 else FpBits64
            } else fp_float_kind_for_width(width),
            None() => fp_float_kind_for_width(width),
        }
        else fp_float_kind_for_width(width)
    }
}

function fp_decoded_operand_image(
    instruction : Decoded_instruction,
    state : Cpu_state,
    operand : Decoded_operand,
    width : int,
) -> (
    Fp_operand_image
) = {
    let raw = if operand.operand_type == OperandType_Fn then {
            state.floating_registers[unsigned(operand.value[3 .. 0])]
        } else {
            if operand.operand_type == OperandType_Rn then {
                state.registers[unsigned(operand.value[3 .. 0])]
            } else {
                if operand.operand_type == OperandType_FEA then {
                    match operand.ea {
                        Some(ea) => if ea.kind == EaKind_immediate then {
                            let (evaluation, _) = evaluate_decoded_ea(operand, ea, state, state.pc, width);
                            evaluation.value
                        } else {
                            operand.value
                        },
                        None() => operand.value,
                    }
                } else {
                    operand.value
                }
            }
        };
    let kind = fp_operand_kind_for(instruction, operand, width);
    struct {
        valid =
               operand.operand_type
            != OperandType_FEA
            |  (match operand.ea {
                   Some(ea) => (ea.kind == EaKind_immediate),
                   None() => false,
               }),
        kind = kind,
        bits = fp_operand_bits_for_kind(kind, raw, width),
    }
}

function fp_initial_slots(
    instruction : Decoded_instruction,
    state : Cpu_state,
    count : int,
    index : int,
    slots : Fp_operand_slots,
) -> (
    Fp_operand_slots
) =
    if index >= count then {
        slots
    } else {
        match fp_source_at(instruction.operands, index, 0) {
            None() => slots,
            Some(operand) => match fp_operand_slot_set(
                slots,
                index,
                fp_decoded_operand_image(instruction, state, operand, operation_width(instruction)),
            ) {
                Some(updated) => fp_initial_slots(instruction, state, count, index + 1, updated),
                None() => slots,
            },
        }
    }

function fp_first_missing_source(slots : Fp_operand_slots, count : int, index : int) -> int =
    if index >= count then {
        count
    } else {
        match fp_operand_slot_get(slots, index) {
            Some(operand) => if operand.valid then {
                fp_first_missing_source(slots, count, index + 1)
            } else {
                index
            },
            None() => count,
        }
    }

function fp_writable_fn_at(operands : list(Decoded_operand), wanted : int, current : int) -> option(Decoded_operand) =
    match operands {
        [||] => None(),
        operand :: tail => if    operand.operand_type
                              == OperandType_Fn
                              &  (operand.access == AccessWrite | operand.access == AccessReadWrite) then {
            if current == wanted then {
                Some(operand)
            } else {
                fp_writable_fn_at(tail, wanted, current + 1)
            }
        } else {
            fp_writable_fn_at(tail, wanted, current)
        },
    }

function fp_contract_selector(instruction : Decoded_instruction) -> bits(64) =
    match fp_semantics(instruction.form.operation) {
        Some(semantics) => fp_approx_cpuid_selector(semantics.contract_id),
        None() => 0x0000000000000000,
    }

function fp_fault_with_causes(state : Cpu_state, operation : Semantic_operation, causes : bits(5)) -> Execution_result = {
    let base = faulted(state, operation, FloatingPointFault, "enabled floating-point exception");
    {
        base with
        fault =
            Some(
                struct {
                    kind = FloatingPointFault,
                    operation = operation,
                    detail = "enabled floating-point exception",
                    error_code = sail_zero_extend(causes, 64),
                    bus_error = false,
                },
            ),
    }
}
