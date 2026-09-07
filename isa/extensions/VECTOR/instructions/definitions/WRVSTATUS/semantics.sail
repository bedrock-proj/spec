function clause register_result_extension(Op_WRVSTATUS) = RegisterZeroExtend

function clause repeat_semantics(Op_WRVSTATUS) = RepeatObserveDestination

function execute_WRVSTATUS(instruction : Decoded_instruction, state : Cpu_state) -> option(Execution_result) =
    Some(uop_program_execute([|
        uop_make(UopVectorStateWrite(), uop_location_make_none(), uop_location_make_none(),
                 uop_location_make_none(), Operand_dst, operation_width(instruction), CommitRegisters),
        uop_make(UopCommit(), uop_location_make_none(), uop_location_make_none(),
                 uop_location_make_none(), Operand_dst, operation_width(instruction), CommitRegisters),
    |], instruction, state))
