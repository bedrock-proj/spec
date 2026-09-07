// Shared uop APIs use domain-first names: uop_<domain>_<verb>[_<detail>].
// Instruction-owned lower_<MNEMONIC>_uops hooks and scattered execute_uop clauses
// are the deliberate verb-first extension points. Boolean predicates end in _p.

struct Uop_operand_lowering = {
  program : list(Uop),
  value : Uop_location,
  address : Uop_location,
  next_temporary : int,
  memory : bool,
}

function uop_program_append(left : list(Uop), right : list(Uop)) -> list(Uop) =
    match left {
        [||] => right,
        head :: tail => head :: uop_program_append(tail, right),
    }

function uop_make(
    kind : Uop_kind,
    source0 : Uop_location,
    source1 : Uop_location,
    destination : Uop_location,
    operand_name : Operand_id,
    width : int,
    commit_kind : Commit_kind,
) -> (
    Uop
) =
    struct {
        kind = kind,
        source0 = source0,
        source1 = source1,
        source2 = uop_location_make_none(),
        destination = destination,
        destination2 = uop_location_make_none(),
        auxiliary = uop_location_make_none(),
        operand_name = operand_name,
        width = width,
        commit_kind = commit_kind,
    }

function uop_make_ternary(
    kind : Uop_kind,
    source0 : Uop_location,
    source1 : Uop_location,
    source2 : Uop_location,
    destination : Uop_location,
    operand_name : Operand_id,
    width : int,
    commit_kind : Commit_kind,
) -> (
    Uop
) =
    {
        uop_make(kind, source0, source1, destination, operand_name, width, commit_kind) with
        source2 = source2,
    }

function uop_make_pair(
    kind : Uop_kind,
    source0 : Uop_location,
    source1 : Uop_location,
    destination : Uop_location,
    destination2 : Uop_location,
    operand_name : Operand_id,
    width : int,
    commit_kind : Commit_kind,
) -> (
    Uop
) =
    {
        uop_make(kind, source0, source1, destination, operand_name, width, commit_kind) with
        destination2 = destination2,
    }

function uop_operand_location(operand : Decoded_operand) -> Uop_location =
    if operand.operand_type == OperandType_Rn then {
        uop_location_make_register(unsigned(operand.value[3 .. 0]))
    } else {
        if operand.operand_type == OperandType_Fn then {
            uop_location_make_fp_register(unsigned(operand.value[3 .. 0]))
        } else if operand.operand_type == OperandType_CS then {
            uop_location_make_segment(0)
        } else if operand.operand_type == OperandType_SREG then {
            uop_location_make_segment(unsigned(operand.value[2 .. 0]) + 1)
        } else {
            if operand.operand_type == OperandType_SP then {
                uop_location_make_sp()
            } else {
                if operand.operand_type == OperandType_EA then {
                    uop_location_make_none()
                } else {
                    uop_location_make_immediate(immediate_value(operand))
                }
            }
        }
    }

function uop_ea_update_location(ea : Decoded_ea, target : Ea_update_target) -> option(Uop_location) =
    match ea_field_role(if target == EaUpdateTarget_base then EaRole_base else EaRole_index, ea.fields) {
        Some(index) => Some(uop_location_make_register(index)),
        None() => None(),
    }

function uop_ea_role_update_program(operand : Decoded_operand, ea : Decoded_ea,
                                    width : int, target : Ea_update_target) -> list(Uop) =
    match (ea_autoupdate_for(target, ea.autoupdates), uop_ea_update_location(ea, target)) {
        (Some(update), Some(destination)) => [|
            uop_make(
                UopIntegerAdd(), destination,
                uop_location_make_ea_update_amount(Some(update.difference), width,
                    update.mode == EaUpdateMode_predecrement),
                destination, operand.name, 8, CommitRegisters,
            ),
        |],
        _ => [||],
    }

function uop_ea_update_program(operand : Decoded_operand, width : int) -> list(Uop) =
    match operand.ea {
        None() => [||],
        Some(ea) => uop_program_append(
            uop_ea_role_update_program(operand, ea, width, EaUpdateTarget_base),
            uop_ea_role_update_program(operand, ea, width, EaUpdateTarget_index)),
    }

function uop_operand_lower(
    operand : Decoded_operand,
    width : int,
    next_temporary : int,
    read_memory : bool,
) -> (
    Uop_operand_lowering
) =
    match operand.ea {
        Some(ea) => if ea.kind == EaKind_memory then {
            let address = uop_location_make_temporary(next_temporary);
            let value = if read_memory then {
                    uop_location_make_temporary(next_temporary + 1)
                } else {
                    uop_location_make_none()
                };
            let calculate = [|uop_make(UopCalcEa(), uop_location_make_none(), uop_location_make_none(), address, operand.name, width, CommitNone)|];
            let load = if read_memory then {
                    [|uop_make(UopMemoryLoad(), address, uop_location_make_none(), value, operand.name, width, CommitMemory)|]
                } else {
                    [||]
                };
            struct {
                program =
                    uop_program_append(
                        calculate,
                        uop_program_append(uop_ea_update_program(operand, width), load),
                    ),
                value = value,
                address = address,
                next_temporary = next_temporary + (if read_memory then 2 else 1),
                memory = true,
            }
        } else {
            let value = uop_location_make_temporary(next_temporary);
            struct {
                program = uop_program_append(
                    [|uop_make(UopCalcEa(), uop_location_make_none(), uop_location_make_none(), value, operand.name, width, CommitNone)|],
                    uop_ea_update_program(operand, width)),
                value = value,
                address = uop_location_make_none(),
                next_temporary = next_temporary + 1,
                memory = false,
            }
        },
        None() => struct {
            program = [||],
            value = uop_operand_location(operand),
            address = uop_location_make_none(),
            next_temporary = next_temporary,
            memory = false,
        },
    }

function uop_program_build(
    preparation : list(Uop),
    body : list(Uop),
    operand_name : Operand_id,
    width : int,
    commit_kind : Commit_kind,
) -> (
    list(Uop)
) =
    uop_program_append(
        preparation,
        uop_program_append(
            body,
            [|
                uop_make(
                    UopCommit(), uop_location_make_none(), uop_location_make_none(), uop_location_make_none(),
                    operand_name, width, commit_kind,
                ),
            |],
        ),
    )

function uop_program_build_operation(
    preparation : list(Uop),
    operation : Uop,
    result : Uop_location,
    destination : Uop_operand_lowering,
    operand_name : Operand_id,
    width : int,
    memory_access : bool,
) -> (
    list(Uop)
) = {
    let store = if destination.memory then {
            [|
                uop_make(
                    UopMemoryStore(),
                    result,
                    destination.address,
                    uop_location_make_none(),
                    operand_name,
                    width,
                    CommitMemory,
                ),
            |]
        } else {
            [||]
        };
    let commit_kind = if memory_access then { CommitMemory } else { CommitRegisters };
    uop_program_build(
        preparation,
        operation :: store,
        operand_name,
        width,
        commit_kind,
    )
}

function uop_program_build_transfer(
    preparation : list(Uop),
    source : Uop_location,
    destination : Uop_operand_lowering,
    operand_name : Operand_id,
    width : int,
    memory_access : bool,
) -> (
    list(Uop)
) = {
    let transfer = if destination.memory then {
            uop_make(
                UopMemoryStore(), source, destination.address, uop_location_make_none(),
                operand_name, width, CommitMemory,
            )
        } else {
            uop_make(
                UopMove(), source, uop_location_make_none(), destination.value,
                operand_name, width, CommitRegisters,
            )
        };
    let commit_kind = if memory_access then { CommitMemory } else { CommitRegisters };
    uop_program_build(
        preparation,
        [|
            transfer,
        |],
        operand_name,
        width,
        commit_kind,
    )
}

function uop_program_build_atomic(
    memory : Uop_operand_lowering,
    first : Uop_location,
    second : Uop_location,
    order : Uop_location,
    operation : Atomic_uop_operation,
    result_operand : Operand_id,
    width : int,
) -> (
    list(Uop)
) = {
    let response = uop_location_make_temporary(memory.next_temporary);
    let issue = {
        uop_make_ternary(
            UopAtomicIssue(operation), memory.address, first, second,
            response, Operand_memory, width, CommitMemory,
        ) with
        auxiliary = order,
    };
    let complete = uop_make(
        UopAtomicComplete(operation), response, first, first,
        result_operand, width, CommitRegisters,
    );
    uop_program_build(
        memory.program, [|issue, complete|], result_operand, width, CommitMemory,
    )
}

function uop_program_build_cache(
    address : Uop_operand_lowering,
    operation : Cache_uop_operation,
    operand_name : Operand_id,
) -> (
    list(Uop)
) = {
    let translated = uop_location_make_temporary(address.next_temporary);
    let translate = uop_make(
        UopCacheTranslate(operation), address.address, uop_location_make_none(), translated,
        operand_name, 8, CommitControl,
    );
    let maintain = uop_make(
        UopCacheMaintain(operation), translated, uop_location_make_none(), uop_location_make_none(),
        operand_name, 8, CommitControl,
    );
    uop_program_build(
        address.program, [|translate, maintain|], operand_name, 8, CommitControl,
    )
}

function uop_program_build_stack_push(
    source : Uop_location,
    operand_name : Operand_id,
) -> (
    list(Uop)
) =
    uop_program_build(
        [||],
        [|
            uop_make(
                UopIntegerAdd(), uop_location_make_sp(), uop_location_make_immediate(bits_from_int(0 - 8)),
                uop_location_make_sp(), operand_name, 8, CommitRegisters,
            ),
            uop_make(
                UopStackRange(true), uop_location_make_sp(), uop_location_make_none(), uop_location_make_none(),
                operand_name, 8, CommitMemory,
            ),
            uop_make(
                UopStackStore(), source, uop_location_make_sp(), uop_location_make_none(),
                operand_name, 8, CommitMemory,
            ),
        |],
        operand_name,
        8,
        CommitMemory,
    )

function uop_program_build_stack_pair_push(
    first : Uop_location,
    second : Uop_location,
    operand_name : Operand_id,
) -> (
    list(Uop)
) =
    uop_program_build(
        [||],
        [|
            uop_make(
                UopIntegerAdd(), uop_location_make_sp(), uop_location_make_immediate(bits_from_int(0 - 16)),
                uop_location_make_sp(), operand_name, 8, CommitRegisters,
            ),
            uop_make(
                UopStackRange(true), uop_location_make_sp(), uop_location_make_none(), uop_location_make_none(),
                operand_name, 16, CommitMemory,
            ),
            uop_make(
                UopStackStorePair(), first, second, uop_location_make_sp(),
                operand_name, 16, CommitMemory,
            ),
        |],
        operand_name,
        16,
        CommitMemory,
    )

function uop_program_build_stack_pop(
    destination : Uop_location,
    operand_name : Operand_id,
) -> (
    list(Uop)
) =
    uop_program_build(
        [||],
        [|
            uop_make(
                UopStackRange(false), uop_location_make_sp(), uop_location_make_none(), uop_location_make_none(),
                operand_name, 8, CommitMemory,
            ),
            uop_make(
                UopStackLoad(0), uop_location_make_sp(), uop_location_make_none(), uop_location_make_temporary(0),
                operand_name, 8, CommitMemory,
            ),
            uop_make(
                UopMove(), uop_location_make_temporary(0), uop_location_make_none(), destination,
                operand_name, 8, CommitRegisters,
            ),
            uop_make(
                UopIntegerAdd(), uop_location_make_sp(), uop_location_make_immediate(bits_from_int(8)),
                uop_location_make_sp(), operand_name, 8, CommitRegisters,
            ),
        |],
        operand_name,
        8,
        CommitRegisters,
    )

function uop_program_build_stack_pair_pop(
    first : Uop_location,
    second : Uop_location,
    operand_name : Operand_id,
) -> (
    list(Uop)
) =
    uop_program_build(
        [||],
        [|
            uop_make(
                UopStackRange(false), uop_location_make_sp(), uop_location_make_none(), uop_location_make_none(),
                operand_name, 16, CommitMemory,
            ),
            uop_make(
                UopStackLoad(0), uop_location_make_sp(), uop_location_make_none(), uop_location_make_temporary(0),
                operand_name, 8, CommitMemory,
            ),
            uop_make(
                UopStackLoad(8), uop_location_make_sp(), uop_location_make_none(), uop_location_make_temporary(1),
                operand_name, 8, CommitMemory,
            ),
            uop_make_pair(
                UopRegisterPairMove(), uop_location_make_temporary(0), uop_location_make_temporary(1),
                first, second, operand_name, 8, CommitRegisters,
            ),
            uop_make(
                UopIntegerAdd(), uop_location_make_sp(), uop_location_make_immediate(bits_from_int(16)),
                uop_location_make_sp(), operand_name, 8, CommitRegisters,
            ),
        |],
        operand_name,
        16,
        CommitRegisters,
    )
