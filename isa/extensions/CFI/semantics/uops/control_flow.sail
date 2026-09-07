function cfi_fault(state : Cpu_state, operation : Semantic_operation,
                   cause : int, detail : string) -> Execution_result =
  faulted_with_error_code(
    state, operation, CfiViolationFault, detail, bits_from_int(cause)
  )

function cfi_landing_request(target : bits(64), cs_image : bits(64))
  -> Primitive_request =
  {
    empty_primitive_request() with
    kind = RepeatBodyFetch,
    role = RequestRoleControlTarget,
    domain = RequestDomainCode,
    segment = 0,
    segment_image = cs_image,
    width = 1,
    range_start = target,
    range_end = target,
    range_length = 0,
    effective_address = target,
    linear_address = segment_point(cs_image, target).linear,
    access = AccessExecute,
    address_translation = true,
    body_length = 0,
  }

function cfi_stack_record_request(kind : Primitive_request_kind,
                                  state : Cpu_state, address : bits(64),
                                  width : int, payload : list(bits(8)),
                                  commit : bool) -> Primitive_request = {
  let point = segment_point(state.segments[2], address);
  {
    uop_stack_request_make(
      kind, state, address, add_bits(address, bits_from_int(width)),
      if kind == Read then AccessStackRead else AccessStackWrite,
      0x0000000000000000, commit
    ) with
    width = width,
    linear_address = point.linear,
    memory_ranges = [|struct {
      effective_address = address,
      linear_address = point.linear,
      width = width,
      buffer_offset = 0,
    }|],
    validation_ranges = [|struct {
      effective_address = address,
      linear_address = point.linear,
      width = width,
    }|],
    body_length = if kind == Read then width else 0,
    payload_bytes = if kind == Write then payload else [||],
  }
}

union clause Uop_kind = UopCfiKeyGuard : unit
union clause Uop_kind = UopCfiLandingFetch : unit
union clause Uop_kind = UopCfiLandingCheck : unit
union clause Uop_kind = UopCfiTag : bool
union clause Uop_kind = UopCfiRecordStore : bool
union clause Uop_kind = UopCfiRecordLoad : int
union clause Uop_kind = UopCfiRecordAuthenticate : bool
union clause Uop_kind = UopCfiResidentAuthenticate : unit

function clause execute_uop(UopCfiKeyGuard(), _, values, instruction,
                            before, speculative) =
  if cfi_key_configured(speculative) then uop_step_continue(speculative, values)
  else uop_step_fault(faulted(
    before, instruction.form.operation, InvalidControlTransitionFault,
    "protected control transfer requires a configured CFI key"
  ))

function clause execute_uop(UopCfiLandingFetch(), uop, values, instruction,
                            before, speculative) =
  match (uop_location_read(uop.source0, speculative, values),
         uop_location_read(uop.source1, speculative, values)) {
    (Some(target), Some(cs_image)) =>
      if not_bool(segment_image_valid(cs_image))
         | not_bool(segment_point(cs_image, target).in_bounds) then
        uop_step_fault(cfi_fault(
          before, instruction.form.operation, 1, "invalid indirect landing target"
        ))
      else uop_step_wait(
        uop.destination.index, cfi_landing_request(target, cs_image), target
      ),
    _ => uop_step_fault(cfi_fault(
      before, instruction.form.operation, 1, "unavailable indirect landing target"
    )),
  }

function clause execute_uop(UopCfiLandingCheck(), uop, values, instruction,
                            before, speculative) =
  match uop_value_get(values, uop.source0.index) {
    None() => uop_step_fault(cfi_fault(
      before, instruction.form.operation, 1, "missing indirect landing response"
    )),
    Some(response) => match decode_full_record(response.body_bytes) {
      Some(target) =>
        if target.form.operation == Op_CFILAND
           & byte_list_length(response.body_bytes) == target.encoded_length then
          uop_step_continue(speculative, values)
        else uop_step_fault(cfi_fault(
          before, instruction.form.operation, 1, "indirect target is not CFILAND"
        )),
      None() => uop_step_fault(cfi_fault(
        before, instruction.form.operation, 1,
        "indirect target is not decodable CFILAND"
      )),
    },
  }

function clause execute_uop(UopCfiTag(long_return), uop, values, instruction,
                            before, speculative) =
  match (uop_location_read(uop.source0, speculative, values),
         uop_location_read(uop.source1, speculative, values)) {
    (Some(continuation_pc), Some(continuation_cs)) =>
      match uop_location_write(
        uop.destination,
        cfi_pointer_authentication(
          speculative, long_return, continuation_pc, continuation_cs
        ),
        64, instruction.form.operation, speculative, values
      ) {
        Some((updated, next_values)) => uop_step_continue(updated, next_values),
        None() => uop_step_fault(faulted(
          before, instruction.form.operation, InvalidControlState,
          "pointer-authentication destination is unavailable"
        )),
      },
    _ => uop_step_fault(faulted(
      before, instruction.form.operation, InvalidControlState,
      "pointer-authentication input is unavailable"
    )),
  }

function clause execute_uop(UopCfiRecordStore(long_return), uop, values,
                            instruction, before, speculative) =
  match (uop_location_read(uop.source0, speculative, values),
         uop_location_read(uop.source1, speculative, values),
         uop_location_read(uop.source2, speculative, values),
         uop_location_read(uop.destination, speculative, values)) {
    (Some(continuation_pc), Some(continuation_cs), Some(pa), Some(address)) => {
      let payload = if long_return then append_bytes(
        le64_bytes(continuation_pc),
        append_bytes(le64_bytes(continuation_cs), le64_bytes(pa))
      ) else append_bytes(le64_bytes(continuation_pc), le64_bytes(pa));
      let width = if long_return then 24 else 16;
      uop_step_wait(
        0 - 1,
        cfi_stack_record_request(
          Write, speculative, address, width, payload, true
        ),
        continuation_pc
      )
    },
    _ => uop_step_fault(faulted(
      before, instruction.form.operation, InvalidControlState,
      "protected return record input is unavailable"
    )),
  }

function clause execute_uop(UopCfiRecordLoad(width), uop, values, instruction,
                            before, speculative) =
  match uop_location_read(uop.source0, speculative, values) {
    Some(address) => uop_step_wait(
      uop.destination.index,
      cfi_stack_record_request(Read, speculative, address, width, [||], false),
      address
    ),
    None() => uop_step_fault(faulted(
      before, instruction.form.operation, InvalidControlState,
      "protected return record address is unavailable"
    )),
  }

function clause execute_uop(UopCfiRecordAuthenticate(long_return), uop, values,
                            instruction, before, speculative) =
  match uop_value_get(values, uop.source0.index) {
    None() => uop_step_fault(faulted(
      before, instruction.form.operation, InvalidControlState,
      "protected return record is unavailable"
    )),
    Some(record) => match (
      read_le_bits(record.body_bytes, 0, 64),
      read_le_bits(record.body_bytes, if long_return then 8 else 0, 64),
      read_le_bits(record.body_bytes, if long_return then 16 else 8, 64)
    ) {
      (Some(continuation_pc), Some(raw_cs), Some(pa_image)) => {
        let continuation_cs = if long_return then raw_cs
                              else 0x0000000000000000;
        let width = if long_return then 24 else 16;
        let expected = cfi_pointer_authentication(
          speculative, long_return, continuation_pc, continuation_cs
        );
        if byte_count(record.body_bytes) != width
           | pa_image[63] == bitone
           | pa_image == 0x0000000000000000
           | pa_image != expected then
          uop_step_fault(cfi_fault(
            before, instruction.form.operation, 2,
            "protected return authentication failed"
          ))
        else match uop_location_write(
          uop.destination, continuation_pc, 64, instruction.form.operation,
          speculative, values
        ) {
          None() => uop_step_fault(faulted(
            before, instruction.form.operation, InvalidControlState,
            "authenticated return target destination is unavailable"
          )),
          Some((updated, next_values)) =>
            if not_bool(long_return) then
              uop_step_continue(updated, next_values)
            else match uop_location_write(
              uop.destination2, continuation_cs, 64,
              instruction.form.operation, updated, next_values
            ) {
              Some((final_state, final_values)) =>
                uop_step_continue(final_state, final_values),
              None() => uop_step_fault(faulted(
                before, instruction.form.operation, InvalidControlState,
                "authenticated return CS destination is unavailable"
              )),
            }
        }
      },
      _ => uop_step_fault(cfi_fault(
        before, instruction.form.operation, 2,
        "protected return record is incomplete"
      )),
    },
  }

function clause execute_uop(UopCfiResidentAuthenticate(), uop, values,
                            instruction, before, speculative) = {
  let pa = link_pa(speculative);
  let expected = cfi_pointer_authentication(
    speculative, false, speculative.lpc, 0x0000000000000000
  );
  if not_bool(link_active(speculative)) | pa == 0x0000000000000000
     | pa != expected then uop_step_fault(cfi_fault(
    before, instruction.form.operation, 2,
    "resident return authentication failed"
  )) else match uop_location_write(
    uop.destination, speculative.lpc, 64, instruction.form.operation,
    speculative, values
  ) {
    Some((updated, next_values)) => uop_step_continue(updated, next_values),
    None() => uop_step_fault(faulted(
      before, instruction.form.operation, InvalidControlState,
      "resident return target destination is unavailable"
    )),
  }
}

function cfi_with_landing_check(program : list(Uop)) -> list(Uop) =
  match program {
    [||] => [||],
    uop :: tail => match uop.kind {
      UopControlProbe() =>
        uop_make(
          UopCfiLandingFetch(), uop.source0, uop.source1,
          uop_location_make_temporary(62), Operand_target, 1, CommitControl
        ) :: uop_make(
          UopCfiLandingCheck(), uop_location_make_temporary(62),
          uop_location_make_none(), uop_location_make_none(), Operand_target,
          1, CommitControl
        ) :: uop :: tail,
      _ => uop :: cfi_with_landing_check(tail),
    },
  }

function cfi_indirect_target(instruction : Decoded_instruction) -> bool =
  match operand_named(instruction, Operand_target) {
    Some(target) => if target.operand_type == OperandType_imm8s
                       | target.operand_type == OperandType_imm16s
                       | target.operand_type == OperandType_imm32s then false
      else match target.ea {
        Some(ea) => ea.kind != EaKind_immediate,
        None() => true,
      },
    None() => false,
  }

function cfi_call_program(program : list(Uop), instruction : Decoded_instruction,
                          state : Cpu_state) -> list(Uop) =
  if cfi_key_configured(state) & cfi_indirect_target(instruction)
  then cfi_with_landing_check(program) else program
