val resume_transaction : (Execution_result, Transaction_response) -> Execution_result
val execute_full : (Decoded_instruction, Cpu_state) -> Execution_result

function signed_width(value : bits(64), width : int) -> int =
  signed(sign_extend_width(value, width))

function width_modulus(width : int) -> int = unsigned(width_mask(width)) + 1
function width_bits(width : int) -> int = width * 8

function narrow_bits(value : bits(64), width : int) -> bits(64) = value & width_mask(width)

function bits_from_int(value : int) -> bits(64) = get_slice_int(64, value, 0)

function immediate_value(operand : Decoded_operand) -> bits(64) =
  if operand.operand_type == OperandType_imm8s then sail_sign_extend(operand.value[7 .. 0], 64)
  else if operand.operand_type == OperandType_imm16s then sail_sign_extend(operand.value[15 .. 0], 64)
  else if operand.operand_type == OperandType_imm32s then sail_sign_extend(operand.value[31 .. 0], 64)
  else operand.value

function local_operand_value(state : Cpu_state, operand : Decoded_operand, width : int) -> option(bits(64)) =
  if operand.operand_type == OperandType_Rn then Some(state.registers[unsigned(operand.value[3 .. 0])])
  else if operand.operand_type == OperandType_SP then Some(state.sp)
  else if operand.operand_type == OperandType_CS then Some(state.segments[0])
  else if operand.operand_type == OperandType_SREG then
    Some(state.segments[unsigned(operand.value[2 .. 0]) + 1])
  else if operand.operand_type == OperandType_EA then match operand.ea {
    Some(ea) => if ea.kind == EaKind_immediate then {
      let (evaluation, _) = evaluate_decoded_ea(operand, ea, state, state.pc, width);
      Some(evaluation.value)
    } else None(),
    None() => None(),
  }
  else Some(immediate_value(operand))

function local_destination(operand : Decoded_operand, width : int) -> option(Commit_destination) =
  if operand.operand_type == OperandType_Rn then Some(struct {
    kind = DestinationRn, index = operand.value[3 .. 0], width = width
  })
  else if operand.operand_type == OperandType_SP then Some(struct {
    kind = DestinationSP, index = 0x0, width = width
  })
  else None()

function write_local_destination(state : Cpu_state, operation : Semantic_operation,
                                 destination : Commit_destination, value : bits(64)) -> Cpu_state = {
  var updated = state;
  if destination.kind == DestinationRn then {
    let index = unsigned(destination.index);
    updated.registers[index] = canonical_register_result(operation, value, destination.width)
  }
  else if destination.kind == DestinationSP then
    updated.sp = value
  else ();
  updated
}

function operand_named(instruction : Decoded_instruction, name : Operand_id) -> option(Decoded_operand) =
  find_operand_by_name(name, instruction.operands)

function operand_ordinal_in(
  name : Operand_id, operands : list(Decoded_operand), ordinal : int
) -> int = match operands {
  [||] => 255,
  operand :: tail => if operand.name == name then ordinal
                     else operand_ordinal_in(name, tail, ordinal + 1),
}

function operand_ordinal(instruction : Decoded_instruction, name : Operand_id) -> int =
  operand_ordinal_in(name, instruction.operands, 0)

function value_named(instruction : Decoded_instruction, state : Cpu_state,
                     name : Operand_id) -> option(bits(64)) =
  match operand_named(instruction, name) {
    Some(operand) => local_operand_value(state, operand, instruction_ea_width(operand, operation_width(instruction))),
    None() => None(),
  }

function destination_named(instruction : Decoded_instruction, name : Operand_id,
                           width : int) -> option(Commit_destination) =
  match operand_named(instruction, name) {
    Some(operand) => local_destination(operand, width),
    None() => None(),
  }

function add_with_flags(lhs : bits(64), rhs : bits(64), carry_in : int,
                        width : int) -> (bits(64), bits(4)) = {
  let modulus = width_modulus(width);
  let left = unsigned(narrow_bits(lhs, width));
  let right = unsigned(narrow_bits(rhs, width));
  let complete = left + right + carry_in;
  let result = bits_from_int(tmod_int(complete, modulus)) & width_mask(width);
  let signed_complete = signed_width(lhs, width) + signed_width(rhs, width) + carry_in;
  let signed_limit = tdiv_int(modulus, 2);
  let overflow = signed_complete < -signed_limit | signed_complete >= signed_limit;
  (result, flags_value(result == 0x0000000000000000, width_sign(result, width),
                       complete >= modulus, overflow))
}

function sub_with_flags(lhs : bits(64), rhs : bits(64), borrow_in : int,
                        width : int) -> (bits(64), bits(4)) = {
  let modulus = width_modulus(width);
  let left = unsigned(narrow_bits(lhs, width));
  let right = unsigned(narrow_bits(rhs, width));
  let complete = left - right - borrow_in;
  let normalized = tmod_int(tmod_int(complete, modulus) + modulus, modulus);
  let result = bits_from_int(normalized) & width_mask(width);
  let signed_complete = signed_width(lhs, width) - signed_width(rhs, width) - borrow_in;
  let signed_limit = tdiv_int(modulus, 2);
  let overflow = signed_complete < -signed_limit | signed_complete >= signed_limit;
  (result, flags_value(result == 0x0000000000000000, width_sign(result, width),
                       left < right + borrow_in, overflow))
}

function bit_count_from(value : bits(64), index : int, limit : int) -> int =
  if index >= limit then 0
  else (if sail_shiftright(value, index)[0] == bitone then 1 else 0)
       + bit_count_from(value, index + 1, limit)

function count_from_low(value : bits(64), index : int, limit : int, bit_value : bit) -> int =
  if index >= limit then limit
  else if sail_shiftright(value, index)[0] == bit_value
       then count_from_low(value, index + 1, limit, bit_value)
  else index

function count_from_high(value : bits(64), remaining : int, bit_value : bit) -> int =
  if remaining <= 0 then 0
  else if sail_shiftright(value, remaining - 1)[0] == bit_value then
    1 + count_from_high(value, remaining - 1, bit_value)
  else 0

function reverse_bytes_int(value : int, remaining : int, accumulated : int) -> int =
  if remaining <= 0 then accumulated
  else reverse_bytes_int(tdiv_int(value, 256), remaining - 1,
                         accumulated * 256 + tmod_int(value, 256))

function reverse_bytes(value : bits(64), width : int) -> bits(64) =
  bits_from_int(reverse_bytes_int(unsigned(narrow_bits(value, width)), width, 0))

function reverse_bits_int(value : int, remaining : int, accumulated : int) -> int =
  if remaining <= 0 then accumulated
  else reverse_bits_int(tdiv_int(value, 2), remaining - 1,
                        accumulated * 2 + tmod_int(value, 2))

function reverse_bits(value : bits(64), width : int) -> bits(64) =
  bits_from_int(reverse_bits_int(unsigned(narrow_bits(value, width)), width * 8, 0))

function carryless_loop(lhs : bits(128), rhs : bits(64), index : int,
                        accumulated : bits(128)) -> bits(128) =
  if index >= 64 then accumulated
  else carryless_loop(lhs, rhs, index + 1,
    if sail_shiftright(rhs, index)[0] == bitone
    then xor_vec(accumulated, sail_shiftleft(lhs, index)) else accumulated)

function carryless_product(value : bits(64), source : bits(64)) -> bits(128) =
  carryless_loop(sail_zero_extend(value, 128), source, 0,
    0x00000000000000000000000000000000)

function carryless_high(product : bits(128), width : int) -> bits(64) =
  if width == 1 then sail_zero_extend(product[15 .. 8], 64)
  else if width == 2 then sail_zero_extend(product[31 .. 16], 64)
  else if width == 4 then sail_zero_extend(product[63 .. 32], 64)
  else product[127 .. 64]

function signed_minimum(width : int) -> int = -tdiv_int(width_modulus(width), 2)

function local_result(state : Cpu_state, instruction : Decoded_instruction,
                      updated : Cpu_state, kind : Commit_kind) -> Execution_result =
  completed(state, { updated with pc = next_pc(state, instruction) }, kind, [||])

function first_binary_operand(operands : list(Decoded_operand)) -> option(Decoded_operand) =
  match operands {
    [||] => None(),
    operand :: tail =>
      if operand.name == Operand_cc then first_binary_operand(tail)
      else if operand.name == Operand_src | operand.name == Operand_lhs then Some(operand)
      else first_binary_operand(tail),
  }

function second_binary_operand(operands : list(Decoded_operand)) -> option(Decoded_operand) =
  match operands {
    [||] => None(),
    operand :: tail =>
      if operand.name == Operand_dst | operand.name == Operand_rhs then Some(operand)
      else second_binary_operand(tail),
  }
