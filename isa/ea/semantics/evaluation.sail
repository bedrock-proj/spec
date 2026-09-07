function ea_field_role(role : Ea_role, fields : list(Decoded_field)) -> option(int) = match fields {
  [||] => None(),
  field :: tail => if field.role == Some(role) then Some(field.value) else ea_field_role(role, tail),
}

function ea_segment_index(ea : Decoded_ea) -> int = match ea.segment {
  Some(EaSegment_CS) => 0,
  Some(EaSegment_SS) => 2,
  Some(EaSegment_explicit) => match ea_field_role(EaRole_segment, ea.fields) {
    Some(index) => index + 1,
    None() => 1,
  },
  _ => 1,
}

function ea_payload_width(role : Ea_payload_role, payloads : list(Decoded_ea_payload)) -> int = match payloads {
  [||] => 0,
  payload :: tail => if payload.definition.role == role then payload.definition.width else ea_payload_width(role, tail),
}

function ea_payload_value(role : Ea_payload_role, payloads : list(Decoded_ea_payload)) -> bits(64) = match payloads {
  [||] => 0x0000000000000000,
  payload :: tail => if payload.definition.role == role then {
    let width = payload.definition.width;
    if payload.definition.signed & width > 0 & width < 64 &
       sail_shiftright(payload.value, width - 1)[0] == bitone
    then payload.value | sail_shiftleft(0xFFFFFFFFFFFFFFFF, width)
    else payload.value
  } else ea_payload_value(role, tail),
}

function staged_register(index : int, value : bits(64)) -> list(Staged_register_update) =
  [|struct { index = get_slice_int(4, index, 0), value = value }|]

function append_staged_register_updates(left : list(Staged_register_update), right : list(Staged_register_update))
  -> list(Staged_register_update) = match left {
  [||] => right,
  update :: tail => update :: append_staged_register_updates(tail, right),
}

function image_register(state : Cpu_state, index : int) -> bits(64) =
  state.registers[unsigned(get_slice_int(4, index, 0))]

function image_segment(state : Cpu_state, index : int) -> bits(64) = match index {
  0 => state.segments[0], 1 => state.segments[1], 2 => state.segments[2],
  3 => state.segments[3], 4 => state.segments[4], 5 => state.segments[5],
  6 => state.segments[6], 7 => state.segments[7], _ => state.segments[8],
}

function ea_width_shift(width : int) -> int =
  if width <= 1 then 0 else 1 + ea_width_shift(tdiv_int(width, 2))

val ea_update_amount : (Ea_update_difference, Cpu_state, int) -> int

scattered function ea_update_amount

function clause ea_update_amount(EaUpdateDifference_scale, _, width) = width

function clause ea_update_amount(EaUpdateDifference_constant_1, _, _) = 1

function ea_autoupdate_for(target : Ea_update_target, updates : list(Ea_autoupdate)) -> option(Ea_autoupdate) =
  match updates {
    [||] => None(),
    update :: tail => if update.target == target then Some(update) else ea_autoupdate_for(target, tail),
  }

function ea_autoupdate_mode(update : option(Ea_autoupdate)) -> option(Ea_update_mode) = match update {
  Some(value) => Some(value.mode), None() => None(),
}

function decoded_ea_update_amount(update : option(Ea_autoupdate), image : Cpu_state, width : int) -> bits(64) =
  match update {
    Some(value) => get_slice_int(64, ea_update_amount(value.difference, image, width), 0),
    None() => 0x0000000000000000,
  }

function evaluate_ea_expression_steps(expression : Ea_expression, stack : list(bits(64)), ea : Decoded_ea,
                                      image : Cpu_state, instruction_pc : bits(64), width : int,
                                      base_value : bits(64), index_value : bits(64)) -> bits(64) =
  match expression {
    [||] => match stack {
      [|value|] => value,
      _ => { assert(false, "invalid EA expression result stack"); 0x0000000000000000 },
    },
    step :: tail => {
      let next_stack : list(bits(64)) = match step {
        EaExprLiteral(value) => value :: stack,
        EaExprField(EaRole_base) => base_value :: stack,
        EaExprField(EaRole_index) => index_value :: stack,
        EaExprField(role) => (match ea_field_role(role, ea.fields) {
          Some(index) => image_segment(image, index + 1),
          None() => 0x0000000000000000,
        }) :: stack,
        EaExprPayload(role) => ea_payload_value(role, ea.payloads) :: stack,
        EaExprScale(difference) => get_slice_int(64, ea_update_amount(difference, image, width), 0) :: stack,
        EaExprGpr(index) => image.registers[unsigned(index)] :: stack,
        EaExprSp() => image.sp :: stack,
        EaExprPc() => instruction_pc :: stack,
        EaExprNegate() => match stack {
          value :: rest => sub_bits(0x0000000000000000, value) :: rest,
          _ => { assert(false, "invalid EA negation stack"); [||] },
        },
        operation => match stack {
          right :: left :: rest => (match operation {
            EaExprAdd() => add_bits(left, right),
            EaExprSubtract() => sub_bits(left, right),
            EaExprMultiply() => get_slice_int(64, unsigned(left) * unsigned(right), 0),
            _ => { assert(false, "invalid EA binary operation"); 0x0000000000000000 },
          }) :: rest,
          _ => { assert(false, "invalid EA binary expression stack"); [||] },
        },
      };
      evaluate_ea_expression_steps(tail, next_stack, ea, image, instruction_pc, width, base_value, index_value)
    },
  }

function evaluate_ea_expression(expression : Ea_expression, ea : Decoded_ea,
                                image : Cpu_state, instruction_pc : bits(64), width : int,
                                base_value : bits(64), index_value : bits(64)) -> bits(64) =
  evaluate_ea_expression_steps(expression, [||], ea, image, instruction_pc, width, base_value, index_value)

struct Segment_point = {
  in_bounds : bool,
  linear : bits(64),
}

function segment_point(image : bits(64), address : bits(64)) -> Segment_point = {
  let mantissa = unsigned(image[6 .. 1]);
  if mantissa == 0 then struct { in_bounds = true, linear = address }
  else {
    let exponent = unsigned(image[11 .. 7]);
    let base = unsigned(image[63 .. 12]) * 4096;
    let span = mantissa * (2 ^ exponent) * 4096;
    let limit = base + span;
    let point = unsigned(address);
    if image[0] == bitone then struct {
      in_bounds = base <= point & point < limit,
      linear = address
    }
    else struct {
      in_bounds = point < span,
      linear = get_slice_int(64, base + point, 0)
    }
  }
}

function evaluate_decoded_ea(operand : Decoded_operand, ea : Decoded_ea,
                             image : Cpu_state, instruction_pc : bits(64), width : int)
  -> (Ea_evaluation, Cpu_state) = {
  var staged = image;
  var updates : list(Staged_register_update) = [||];
  let base_index = ea_field_role(EaRole_base, ea.fields);
  let index_index = ea_field_role(EaRole_index, ea.fields);
  let base_update = ea_autoupdate_for(EaUpdateTarget_base, ea.autoupdates);
  let index_update = ea_autoupdate_for(EaUpdateTarget_index, ea.autoupdates);
  let base_amount = decoded_ea_update_amount(base_update, image, width);
  let index_amount = decoded_ea_update_amount(index_update, image, width);
  var base_value : bits(64) = match base_index {
      Some(index) => image_register(image, index),
      None() => 0x0000000000000000,
    };
  if ea_autoupdate_mode(base_update) == Some(EaUpdateMode_predecrement) then match base_index {
    Some(index) => {
      let reg_index = unsigned(get_slice_int(4, index, 0));
      let changed = sub_bits(image.registers[reg_index], base_amount);
      staged.registers[reg_index] = changed; base_value = changed;
      updates = staged_register(index, changed)
    },
    None() => (),
  } else ();
  if ea_autoupdate_mode(base_update) == Some(EaUpdateMode_postincrement) then match base_index {
    Some(index) => {
      let reg_index = unsigned(get_slice_int(4, index, 0));
      let changed = add_bits(image.registers[reg_index], base_amount);
      staged.registers[reg_index] = changed; updates = staged_register(index, changed)
    },
    None() => (),
  } else ();
  var index_value : bits(64) = match index_index {
    Some(index) => image_register(staged, index),
    None() => 0x0000000000000000,
  };
  if ea_autoupdate_mode(index_update) == Some(EaUpdateMode_predecrement) then match index_index {
    Some(index) => {
      let reg_index = unsigned(get_slice_int(4, index, 0));
      let changed = sub_bits(staged.registers[reg_index], index_amount);
      staged.registers[reg_index] = changed; index_value = changed;
      updates = append_staged_register_updates(updates, staged_register(index, changed))
    },
    None() => (),
  } else ();
  if ea_autoupdate_mode(index_update) == Some(EaUpdateMode_postincrement) then match index_index {
    Some(index) => {
      let reg_index = unsigned(get_slice_int(4, index, 0));
      let changed = add_bits(staged.registers[reg_index], index_amount);
      staged.registers[reg_index] = changed; updates = append_staged_register_updates(updates, staged_register(index, changed))
    },
    None() => (),
  } else ();
  let offset = evaluate_ea_expression(ea.expression, ea, staged, instruction_pc, width, base_value, index_value);
  let direct_value = offset;
  let point = if ea.kind == EaKind_immediate then direct_value else offset;
  let segment = ea_segment_index(ea);
  let selected_segment = image_segment(image, segment);
  let translated = segment_point(selected_segment, point);
  let linear = translated.linear;
  (struct {
    operand_name = operand.name, kind = ea.kind, role = operand.ea_role,
    domain = operand.domain, segment = segment, segment_image = selected_segment, width = width,
    effective_address = point, linear_address = linear, value = direct_value,
    staged_updates = updates
  }, staged)
}

function evaluate_decoded_vector_ea(operand : Decoded_operand, ea : Decoded_ea,
                                    image : Cpu_state, instruction_pc : bits(64),
                                    width : int) -> (Ea_evaluation, Cpu_state) =
  evaluate_decoded_ea(operand, { ea with kind = EaKind_memory }, image, instruction_pc, width)

function evaluate_vector_instruction_ea(operands : list(Decoded_operand), image : Cpu_state,
                                        instruction_pc : bits(64), width : int)
  -> option((Ea_evaluation, Cpu_state)) = match operands {
  [||] => None(),
  operand :: tail => match operand.ea {
    Some(ea) => Some(evaluate_decoded_vector_ea(operand, ea, image, instruction_pc, width)),
    None() => evaluate_vector_instruction_ea(tail, image, instruction_pc, width),
  }
}

function evaluate_ea_operands_in_order(operands : list(Decoded_operand), image : Cpu_state,
                                       instruction_pc : bits(64), width : int)
  -> Ea_evaluation_set = match operands {
  [||] => struct { evaluations = [||], image = image },
  operand :: tail => match operand.ea {
    None() => evaluate_ea_operands_in_order(tail, image, instruction_pc, width),
    Some(ea) => {
      let (evaluation, next_image) = evaluate_decoded_ea(operand, ea, image, instruction_pc, width);
      let rest = evaluate_ea_operands_in_order(tail, next_image, instruction_pc, width);
      struct { evaluations = evaluation :: rest.evaluations, image = rest.image }
    },
  }
}

function instruction_ea_width(operand : Decoded_operand, default_width : int) -> int =
  if operand.ea_width == Some(EaWidth_B) then 1
  else if operand.ea_width == Some(EaWidth_W) then 2
  else if operand.ea_width == Some(EaWidth_L) then 4
  else if operand.ea_width == Some(EaWidth_Q) then 8
  else default_width

function evaluate_instruction_eas(operands : list(Decoded_operand),
                                   image : Cpu_state, instruction_pc : bits(64),
                                   default_width : int) -> Ea_evaluation_set = match operands {
  [||] => struct { evaluations = [||], image = image },
  operand :: tail => match operand.ea {
    None() => evaluate_instruction_eas(tail, image, instruction_pc, default_width),
    Some(ea) => {
      let width = instruction_ea_width(operand, default_width);
      let (evaluation, next_image) = evaluate_decoded_ea(
        operand, ea, image, instruction_pc, width
      );
      let rest = evaluate_instruction_eas(
        tail, next_image, instruction_pc, default_width
      );
      struct { evaluations = evaluation :: rest.evaluations, image = rest.image }
    },
  }
}
