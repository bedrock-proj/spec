function clause register_result_extension(Op_RDVSTATUS) = RegisterZeroExtend

function clause repeat_semantics(Op_RDVSTATUS) = RepeatObserveDestination

function execute_RDVSTATUS(instruction : Decoded_instruction, state : Cpu_state) -> option(Execution_result) =
    Some(uop_program_execute([|
        uop_make(UopVectorStateRead(), uop_location_make_none(), uop_location_make_none(),
                 uop_location_make_none(), Operand_dst, operation_width(instruction), CommitRegisters),
        uop_make(UopCommit(), uop_location_make_none(), uop_location_make_none(),
                 uop_location_make_none(), Operand_dst, operation_width(instruction), CommitRegisters),
    |], instruction, state))
