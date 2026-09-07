function segment_image_valid(image : bits(64)) -> bool = {
  let exponent = unsigned(image[5 .. 0]);
  if exponent > 52 then false
  else {
    let base = unsigned(image[63 .. 12]);
    let span_bits : bits(64) = if exponent == 52 then 0x0000000000000000
      else sail_shiftleft(0x0000000000000001, exponent);
    base + unsigned(span_bits) <= width_modulus(8)
  }
}

function sreg_index(operand : Decoded_operand) -> range(0, 8) =
  if operand.operand_type == OperandType_CS then 0 else unsigned(operand.value[2 .. 0]) + 1

function control_register_value(state : Cpu_state, control : Control_register)
  -> bits(64) = control_state_value(state.controls, control)

function control_register_with_value(state : Cpu_state, control : Control_register,
                                     value : bits(64)) -> Cpu_state = {
  var updated = state;
  updated.controls = control_state_with_value(updated.controls, control, value);
  updated
}

function selected_control_is(selected : option(Control_register),
                             expected : Control_register) -> bool =
  match selected {
    Some(control) => control == expected,
    None() => false,
  }

val control_register_is_user_bank : Control_register -> bool

scattered function control_register_is_user_bank

val control_register_write_needs_transition :
  (Control_register, bits(64), Cpu_state) -> bool

scattered function control_register_write_needs_transition

val control_register_write_fault :
  (Control_register, bits(64), Cpu_state, Cpu_state) -> option(Fault_kind)

scattered function control_register_write_fault

val control_register_written_value :
  (Control_register, bits(64), Cpu_state) -> bits(64)

scattered function control_register_written_value

val paging_canonical : (bits(64), bits(64)) -> bool

function event_stack_pair_valid(segment_image : bits(64), stack_top : bits(64)) -> bool =
  segment_image_valid(segment_image)
  & (stack_top & 0x000000000000003F) == 0x0000000000000000
  & segment_point(segment_image, sub_bits(stack_top, 0x0000000000000060)).in_bounds

function event_control_bank_valid(state : Cpu_state) -> bool =
  segment_image_valid(control_register_value(state, ControlRegister_BASE_ECS))
  & segment_image_valid(control_register_value(state, ControlRegister_BASE_EDS))
  & (control_register_value(state, ControlRegister_BASE_EPC)
     & 0x000000000000000F) == 0x0000000000000000
  & paging_canonical(
      control_register_value(state, ControlRegister_BASE_EPC),
      control_register_value(state, ControlRegister_BASE_PTCR)
    )
  & segment_point(
      control_register_value(state, ControlRegister_BASE_ECS),
      control_register_value(state, ControlRegister_BASE_EPC)
    ).in_bounds
  & event_stack_pair_valid(
      control_register_value(state, ControlRegister_BASE_SSS),
      control_register_value(state, ControlRegister_BASE_SSP)
    )
  & event_stack_pair_valid(
      control_register_value(state, ControlRegister_BASE_ISS),
      control_register_value(state, ControlRegister_BASE_ISP)
    )
  & event_stack_pair_valid(
      control_register_value(state, ControlRegister_BASE_FSS),
      control_register_value(state, ControlRegister_BASE_FSP)
    )
  & event_stack_pair_valid(
      control_register_value(state, ControlRegister_BASE_DSS),
      control_register_value(state, ControlRegister_BASE_DSP)
    )

function uinfo_event_code_valid(value : bits(64)) -> bool = {
  let class = unsigned(value[31 .. 24]);
  let id = value[23 .. 0];
  value[63 .. 32] == 0x00000000
  & ((class == 0 & (id == 0x000000 | id == 0x000001 | id == 0x000002
                    | id == 0x000003 | id == 0x000006 | id == 0x000007
                    | id == 0x000008 | id == 0x000009 | id == 0x00000A
                    | id == 0x00000B | id == 0x00000D | id == 0x00000E
                    | id == 0x000018 | id == 0x000019 | id == 0x00001A
                    | id == 0x000020))
     | class == 1 | class == 2)
}

function user_return_bank_image_valid(state : Cpu_state) -> bool = {
  let uctl = control_register_value(state, ControlRegister_BASE_UCTL);
  let saved_status = sail_shiftright(uctl, 16) & 0x000000000000FFFF;
  let saved_flags = uctl & 0x000000000000FFFF;
  uctl[32] == bitone
  & (uctl & 0xFFFFFFFE00000000) == 0x0000000000000000
  & (saved_status & 0xFFFFFFFFFFFFF800) == 0x0000000000000000
  & (saved_status & 0x00000000000007D1) == 0x0000000000000000
  & (saved_flags & 0xFFFFFFFFFFFFFFF0) == 0x0000000000000000
  & uinfo_event_code_valid(
      control_register_value(state, ControlRegister_BASE_UINFO)
    )
  & segment_image_valid(control_register_value(state, ControlRegister_BASE_UCS))
  & segment_image_valid(control_register_value(state, ControlRegister_BASE_UDS))
  & segment_image_valid(control_register_value(state, ControlRegister_BASE_USS))
  & segment_point(
      control_register_value(state, ControlRegister_BASE_USS),
      control_register_value(state, ControlRegister_BASE_USP)
    ).in_bounds
  & paging_canonical(
      control_register_value(state, ControlRegister_BASE_UPC),
      control_register_value(state, ControlRegister_BASE_PTCR)
    )
  & segment_point(
      control_register_value(state, ControlRegister_BASE_UCS),
      control_register_value(state, ControlRegister_BASE_UPC)
    ).in_bounds
}

function state_with_control_write(state : Cpu_state, selector : int,
                                  value : bits(64)) -> Cpu_state = {
  var candidate = state;
  match control_register_from_selector(selector) {
    Some(control) => candidate = control_register_with_value(
      candidate, control, control_register_written_value(control, value, state)
    ),
    None() => (),
  };
  candidate
}

function target_probe_request(ordinal : int, segment_image : bits(64), target : bits(64))
  -> Primitive_request = {
  let point = segment_point(segment_image, target);
  struct {
    kind = TranslationExecuteProbe, ordinal = ordinal, operand = 255,
    role = RequestRoleControlTarget, domain = RequestDomainCode,
    segment = 0, segment_image = segment_image, width = 1, range_start = target,
    range_end = add_bits(target, 0x0000000000000001), effective_address = target,
    range_length = 1, range_wrap = false,
    range_end_at_modulus = target == 0xFFFFFFFFFFFFFFFF,
    linear_address = point.linear, access = AccessExecute, value = 0x0000000000000000,
    address_translation = true, debug_validation = false, debug_validated = false,
    physical_address = 0x0000000000000000, memory_ranges = [||], validation_ranges = [||],
    read_completion = ReadCompletionData, memory_cache_hint = MemoryCacheDefault,
    commit_point = false, memory_order = ordinal,
    cache_policy = 0, suppress_fault = false, staged_updates = [||],
    selector = 0x0000000000000000, expected = 0x0000000000000000,
    desired = 0x0000000000000000, auxiliary = 0x0000000000000000, body_length = 0,
    payload_bytes = [||]
  }
}

function read_state_register(instruction : Decoded_instruction, state : Cpu_state,
                             value : bits(64)) -> Execution_result =
  match destination_named(instruction, Operand_dst, 8) {
    Some(destination) => {
      let updated = write_local_destination(
        state, instruction.form.operation, destination, value
      );
      local_result(state, instruction, updated, CommitRegisters)
    },
    None() => faulted(state, instruction.form.operation, IllegalInstruction,
                      "missing state-register destination"),
  }
