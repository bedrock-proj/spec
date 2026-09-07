function decode_ea_payloads(layout : list(Ea_payload), bytes : list(bits(8)), cursor : int)
  -> option(list(Decoded_ea_payload)) = match layout {
  [||] => Some([||]),
  definition :: tail => match read_le_bits(bytes, cursor, definition.width) {
    None() => None(),
    Some(value) => match decode_ea_payloads(tail, bytes, cursor + tdiv_int(definition.width, 8)) {
      None() => None(),
      Some(rest) => Some(struct { definition = definition, value = value } :: rest),
    },
  },
}

function decode_ea_binding_payloads(bindings : list(Ea_binding), bytes : list(bits(8)),
                                    cursor : int) -> option(Ea_parse) =
  match bindings {
    [||] => Some(struct { bindings = [||], cursor = cursor }),
    binding :: tail => {
      let width = ea_payload_bits(binding.ea.payload_layout);
      match decode_ea_payloads(binding.ea.payload_layout, bytes, cursor) {
        None() => None(),
        Some(payloads) => match decode_ea_binding_payloads(
          tail, bytes, cursor + tdiv_int(width, 8)
        ) {
          None() => None(),
          Some(parsed) => Some(struct {
            bindings = { binding with ea = { binding.ea with payloads = payloads } }
                       :: parsed.bindings,
            cursor = parsed.cursor
          }),
        },
      }
    },
  }

function decode_standalone_payloads(payloads : list(Catalog_payload),
                                    bytes : list(bits(8)), cursor : int)
  -> option(Payload_parse) = match payloads {
  [||] => Some(struct { payloads = [||], cursor = cursor }),
  payload :: tail => match read_le_bits(bytes, cursor, payload.width) {
    None() => None(),
    Some(value) => match decode_standalone_payloads(
      tail, bytes, cursor + tdiv_int(payload.width, 8)
    ) {
      None() => None(),
      Some(parsed) => Some(struct {
        payloads = struct { operand_name = payload.operand_name, value = value }
                   :: parsed.payloads,
        cursor = parsed.cursor
      }),
    },
  },
}

function find_decoded_payload(name : Operand_id, payloads : list(Decoded_payload))
  -> option(Decoded_payload) = match payloads {
  [||] => None(),
  payload :: tail => if payload.operand_name == name then Some(payload)
                     else find_decoded_payload(name, tail),
}

function assemble_operand_list(operands : list(Catalog_operand),
                               fields : list(Decoded_field), bindings : list(Ea_binding),
                               payloads : list(Decoded_payload))
  -> option(list(Decoded_operand)) =
  match operands {
    [||] => Some([||]),
    operand :: tail => {
      let ea = find_ea_binding(operand.name, bindings);
      let field : option(Decoded_field) = match operand.field_symbol {
        Some(symbol) => find_decoded_field(symbol, fields),
        None() => None(),
      };
      let appended = find_decoded_payload(operand.name, payloads);
      let value_result : option(bits(64)) = match (field, appended) {
        (Some(item), _) => Some(get_slice_int(64, item.value, 0)),
        (_, Some(item)) => Some(item.value),
        _ => if operand.has_fixed_value
             then Some(get_slice_int(64, operand.fixed_value, 0))
             else Some(0x0000000000000000),
      };
      match value_result {
        Some(value) => match assemble_operand_list(tail, fields, bindings, payloads) {
          None() => None(),
          Some(parsed) => Some(
            struct {
              name = operand.name,
              operand_type = operand.operand_type,
              access = operand.access,
              domain = operand.domain,
              ea_role = operand.ea_role,
              ea_width = operand.ea_width,
              value = value,
              ea = ea
            } :: parsed
          ),
        },
        None() => None(),
      }
    },
  }

function decode_operand_list(operands : list(Catalog_operand), payloads : list(Catalog_payload),
                             fields : list(Decoded_field), bindings : list(Ea_binding),
                             bytes : list(bits(8)), cursor : int) -> option(Operand_parse) =
  match decode_ea_binding_payloads(bindings, bytes, cursor) {
    None() => None(),
    Some(eas) => match decode_standalone_payloads(payloads, bytes, eas.cursor) {
      None() => None(),
      Some(appended) => match assemble_operand_list(
        operands, fields, eas.bindings, appended.payloads
      ) {
        None() => None(),
        Some(decoded) => Some(struct { operands = decoded, cursor = appended.cursor }),
      },
    },
  }

function ea_payload_failure_stage(bindings : list(Ea_binding), bytes : list(bits(8)),
                                  cursor : int) -> Decode_stage = match bindings {
  [||] => DecodeSuccess,
  binding :: tail => {
    let width = ea_payload_bits(binding.ea.payload_layout);
    let next_cursor = cursor + tdiv_int(width, 8);
    if byte_count(bytes) < next_cursor then DecodeEaPayload
    else ea_payload_failure_stage(tail, bytes, next_cursor)
  },
}

function ea_binding_payload_bytes(bindings : list(Ea_binding)) -> int = match bindings {
  [||] => 0,
  binding :: tail => tdiv_int(ea_payload_bits(binding.ea.payload_layout), 8)
                     + ea_binding_payload_bytes(tail),
}

function standalone_payload_failure_stage(payloads : list(Catalog_payload),
                                          bytes : list(bits(8)), cursor : int)
  -> Decode_stage = match payloads {
  [||] => DecodeSuccess,
  payload :: tail => {
    let next_cursor = cursor + tdiv_int(payload.width, 8);
    if byte_count(bytes) < next_cursor then DecodeStandalonePayload
    else standalone_payload_failure_stage(tail, bytes, next_cursor)
  },
}

function operand_payload_failure_stage(payloads : list(Catalog_payload),
                                       bindings : list(Ea_binding), bytes : list(bits(8)),
                                       cursor : int) -> Decode_stage = {
  let ea_stage = ea_payload_failure_stage(bindings, bytes, cursor);
  if ea_stage != DecodeSuccess then ea_stage
  else standalone_payload_failure_stage(
    payloads, bytes, cursor + ea_binding_payload_bytes(bindings)
  )
}

function int_member(value : int, values : list(int)) -> bool = match values {
  [||] => false,
  item :: tail => value == item | int_member(value, tail),
}

function legal_value_or_unrestricted(value : int, values : list(int)) -> bool = match values {
  [||] => true,
  _ => int_member(value, values),
}

function operands_statically_legal(catalog : list(Catalog_operand),
                                   decoded : list(Decoded_operand)) -> bool =
  match (catalog, decoded) {
    ([||], [||]) => true,
    (expected :: expected_tail, actual :: actual_tail) => {
      let fixed_ok = not_bool(expected.has_fixed_value) | unsigned(actual.value) == expected.fixed_value;
      let enum_ok = legal_value_or_unrestricted(unsigned(actual.value), expected.legal_values);
      fixed_ok & enum_ok
      & operands_statically_legal(expected_tail, actual_tail)
    },
    _ => false,
  }

function decode_success(form : Catalog_entry, encoding : bits(64), fields : list(Decoded_field),
                        operands : Operand_parse, frame : Framed_record,
                        bytes : list(bits(8))) -> Decode_outcome = {
  let instruction : Decoded_instruction = struct {
    form = form, encoding_bits = encoding, fields = fields, operands = operands.operands,
    encoded_length = frame.encoded_length, required_length = operands.cursor, bytes = bytes
  };
  if not_bool(operands_statically_legal(form.operands, operands.operands)) then
    struct { stage = DecodeStaticLegality, candidate_form = Some(form), instruction = None(), detail = "fixed or enum operand" }
  else if not_bool(required_bytes_sufficient(frame, operands.cursor)) then
    struct { stage = DecodeRecordLength, candidate_form = Some(form), instruction = None(), detail = "record length" }
  else struct { stage = DecodeSuccess, candidate_form = None(), instruction = Some(instruction), detail = "" }
}

function decode_full_record_execution_outcome(bytes : list(bits(8))) -> Decode_outcome =
  if byte_count(bytes) == 0 then
    struct { stage = DecodeFetch, candidate_form = None(), instruction = None(), detail = "instruction byte 0 unavailable" }
  else match frame_record(bytes) {
    None() => struct { stage = DecodeFraming, candidate_form = None(), instruction = None(), detail = "record framing" },
    Some(frame) => {
      let encoding_class = encoding_class_of_record(frame.record_class);
      match read_encoding_bits(bytes, encoding_class) {
        None() => struct { stage = DecodeFetch, candidate_form = None(), instruction = None(), detail = "opcode-space bytes unavailable" },
        Some(encoding) => match find_catalog_entry(encoding, encoding_class, primary_form_catalog_for(encoding_class)) {
          None() => match find_opcode_entry(encoding, encoding_class, primary_form_catalog_for(encoding_class)) {
            None() => struct { stage = DecodeOpcode, candidate_form = None(), instruction = None(), detail = "unassigned opcode" },
            Some(opcode_form) => struct {
              stage = DecodeConstraint, candidate_form = Some(opcode_form),
              instruction = None(), detail = "catalog constraint"
            },
          },
          Some(form) => {
            let fields = decode_fields(encoding, form.fields);
            let opcode_space_bytes = class_minimum(frame.record_class);
            match parse_ea_descriptors(form.operands, fields, bytes, opcode_space_bytes) {
              None() => {
                struct {
                  stage = ea_failure_stage(form.operands, fields, bytes, opcode_space_bytes),
                  candidate_form = Some(form), instruction = None(), detail = "effective-address encoding"
                }
              },
              Some(eas) => match decode_operand_list(
                form.operands, form.appended_payloads, fields, eas.bindings, bytes, eas.cursor
              ) {
                None() => struct {
                  stage = operand_payload_failure_stage(
                    form.appended_payloads, eas.bindings, bytes, eas.cursor
                  ), instruction = None(),
                  candidate_form = Some(form),
                  detail = "operand payload"
                },
                Some(operands) => decode_success(form, encoding, fields, operands, frame, bytes),
              },
            }
          },
        },
      }
    },
  }

function decode_full_record_outcome(bytes : list(bits(8))) -> Decode_outcome = {
  let decoded = decode_full_record_execution_outcome(bytes);
  match decoded.instruction {
    Some(instruction) => { decoded with candidate_form = Some(instruction.form) },
    None() => decoded,
  }
}

function decode_full_record(bytes : list(bits(8))) -> option(Decoded_instruction) =
  match decode_full_record_outcome(bytes).instruction {
    Some(instruction) => Some(instruction),
    None() => None(),
  }
