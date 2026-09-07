function flag_z(flags : bits(4)) -> bool = flags[3] == bitone
function flag_n(flags : bits(4)) -> bool = flags[2] == bitone
function flag_c(flags : bits(4)) -> bool = flags[1] == bitone
function flag_v(flags : bits(4)) -> bool = flags[0] == bitone

function condition_holds(condition : int, flags : bits(4)) -> bool = match condition {
    0 => true,
    1 => false,
    2 => flag_z(flags),
    3 => not_bool(flag_z(flags)),
    4 => flag_c(flags),
    5 => not_bool(flag_c(flags)),
    6 => flag_n(flags),
    7 => not_bool(flag_n(flags)),
    8 => flag_v(flags),
    9 => not_bool(flag_v(flags)),
    10 => flag_c(flags) | flag_z(flags),
    11 => not_bool(flag_c(flags)) & not_bool(flag_z(flags)),
    12 => flag_n(flags) != flag_v(flags),
    13 => flag_n(flags) == flag_v(flags),
    14 => flag_z(flags) | (flag_n(flags) != flag_v(flags)),
    _ => not_bool(flag_z(flags)) & (flag_n(flags) == flag_v(flags)),
}

function condition_field(fields : list(Decoded_field)) -> option(int) = match fields {
    [||] => None(),
    field :: tail => if field.kind == FieldCondition then {
        Some(field.value)
    } else {
        condition_field(tail)
    },
}

function predicate_holds(instruction : Decoded_instruction, state : Cpu_state) -> bool =
    match condition_field(instruction.fields) {
        Some(condition) => condition_holds(condition, state.flags),
        None() => true,
    }

function find_operand_by_name(name : Operand_id, operands : list(Decoded_operand)) -> option(Decoded_operand) =
    match operands {
        [||] => None(),
        operand :: tail => if operand.name == name then {
            Some(operand)
        } else {
            find_operand_by_name(name, tail)
        },
    }

function base_cpuid_flag_enabled(flag : Cpuid_flag, state : Cpu_state) -> bool =
  match flag {
    CpuidFlag_MOVACQ => state.base_instruction_features[0] == bitone,
    CpuidFlag_MOVREL => state.base_instruction_features[1] == bitone,
    CpuidFlag_FETCHXCHG => state.base_instruction_features[2] == bitone,
    CpuidFlag_CMPXCHGP => state.base_instruction_features[3] == bitone,
    CpuidFlag_SPECFENCE => state.base_instruction_features[4] == bitone,
    CpuidFlag_BIC => state.base_instruction_features[5] == bitone,
    CpuidFlag_BFEXTU => state.base_instruction_features[6] == bitone,
    CpuidFlag_BFEXTS => state.base_instruction_features[7] == bitone,
    CpuidFlag_BFINS => state.base_instruction_features[8] == bitone,
    CpuidFlag_REVBIT => state.base_instruction_features[9] == bitone,
    CpuidFlag_FETCHANDN => state.base_instruction_features[10] == bitone,
    CpuidFlag_FETCHMINS => state.base_instruction_features[11] == bitone,
    CpuidFlag_FETCHMINU => state.base_instruction_features[12] == bitone,
    CpuidFlag_FETCHMAXS => state.base_instruction_features[13] == bitone,
    CpuidFlag_FETCHMAXU => state.base_instruction_features[14] == bitone,
    CpuidFlag_MADD => state.base_instruction_features[15] == bitone,
    CpuidFlag_MSUB => state.base_instruction_features[16] == bitone,
    CpuidFlag_LDP => state.base_instruction_features[17] == bitone,
    CpuidFlag_STP => state.base_instruction_features[18] == bitone,
    CpuidFlag_CRC32 => state.base_instruction_features[19] == bitone,
    CpuidFlag_CRC32C => state.base_instruction_features[20] == bitone,
    CpuidFlag_FP => state.fp_enabled,
    CpuidFlag_FP16_CONVERT => state.fp16_convert_enabled,
    CpuidFlag_FPTRANSA => state.fptrans_enabled,
    CpuidFlag_VECTOR => state.vector_enabled,
    CpuidFlag_VECTORFP => state.fp_enabled & state.vector_enabled,
    CpuidFlag_WAIT => state.wait_enabled,
  }

val cpuid_flag_enabled : (Cpuid_flag, Cpu_state) -> bool

function cpuid_flags_available(flags : list(Cpuid_flag), state : Cpu_state) -> bool =
    match flags {
        [||] => true,
        flag :: tail => cpuid_flag_enabled(flag, state) & cpuid_flags_available(tail, state),
    }

function instruction_available(instruction : Decoded_instruction, state : Cpu_state) -> bool =
    cpuid_flags_available(instruction.form.required_cpuid_flags, state)

function candidate_form_available(form : Catalog_entry, state : Cpu_state) -> bool =
    cpuid_flags_available(form.required_cpuid_flags, state)

function first_ea_operand(operands : list(Decoded_operand)) -> option(Decoded_operand) = match operands {
    [||] => None(),
    operand :: tail => match operand.ea {
        Some(_) => Some(operand),
        None() => first_ea_operand(tail),
    },
}

function commit_destination_for(operands : list(Decoded_operand), width : int) -> Commit_destination =
    match operands {
        [||] => empty_destination(),
        operand :: tail => if operand.access == AccessWrite | operand.access == AccessReadWrite then {
            if operand.operand_type == OperandType_Rn then {
                struct { kind = DestinationRn, index = operand.value[3 .. 0], width = width }
            } else {
                if operand.operand_type == OperandType_Fn then {
                    struct { kind = DestinationFn, index = operand.value[3 .. 0], width = width }
                } else {
                    if operand.operand_type == OperandType_SP then {
                        struct { kind = DestinationSP, index = 0x0, width = width }
                    } else {
                        commit_destination_for(tail, width)
                    }
                }
            }
        } else {
            commit_destination_for(tail, width)
        },
    }

function size_code_bytes(code : Size_code) -> int = match code {
    Size_B => 1,
    Size_W => 2,
    Size_H => 2,
    Size_S => 4,
    Size_L => 4,
    _ => 8,
}

function size_at(items : list((int, Size_code)), selector : int) -> Size_code = match items {
    [||] => Size_Q,
    (value, code) :: tail => if value == selector then code else size_at(tail, selector),
}

val condition_size_field : list(Decoded_field) -> option(int)

function operation_width(instruction : Decoded_instruction) -> int =
    match condition_size_field(instruction.fields) {
        Some(selector) => size_code_bytes(size_at(instruction.form.sizes, selector)),
        None() => match instruction.form.sizes {
            (_, code) :: _ => size_code_bytes(code),
            [||] => 8,
        },
    }

function condition_size_field(fields : list(Decoded_field)) -> option(int) = match fields {
    [||] => None(),
    field :: tail => if field.kind == FieldSize then {
        Some(field.value)
    } else {
        condition_size_field(tail)
    },
}
