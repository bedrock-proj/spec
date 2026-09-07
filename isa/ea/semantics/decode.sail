function ea_patterns_match(patterns : list(Ea_pattern), bytes : list(bits(8)), cursor : int) -> bool =
  match patterns {
    [||] => true,
    pattern :: tail => match byte_at(bytes, cursor) {
      Some(byte) =>
        ((sail_zero_extend(byte, 16) & pattern.mask) == pattern.value)
        & ea_patterns_match(tail, bytes, cursor + 1),
      None() => false,
    },
  }

function ea_pattern_count(patterns : list(Ea_pattern)) -> int = match patterns {
  [||] => 0,
  _ :: tail => 1 + ea_pattern_count(tail),
}

function compact_ea_matches(form : Ea_form, profile : Ea_profile, raw : bits(16)) -> bool = match form.patterns {
  [|pattern|] => (form.profile == Some(profile)) & (form.descriptor_family == None())
                 & ((raw & pattern.mask) == pattern.value),
  _ => false,
}

function find_compact_ea(profile : Ea_profile, raw : bits(16), forms : list(Ea_form)) -> option(Ea_form) = match forms {
  [||] => None(),
  form :: tail => if compact_ea_matches(form, profile, raw) then Some(form)
                  else find_compact_ea(profile, raw, tail),
}

function find_extended_ea(profile : Ea_profile, family : Ea_descriptor_family,
                          bytes : list(bits(8)), cursor : int,
                          forms : list(Ea_form)) -> option(Ea_form) =
  match forms {
    [||] => None(),
    form :: tail =>
      if (form.profile == Some(profile)) & (form.descriptor_family == Some(family))
         & ea_patterns_match(form.patterns, bytes, cursor)
      then Some(form)
      else find_extended_ea(profile, family, bytes, cursor, tail),
  }

val extracted_ea_field : (bits(64), list(nat), int) -> int
val decode_ea_field_list : (bits(64), list(Ea_field)) -> list(Decoded_field)

function append_decoded_fields(left : list(Decoded_field), right : list(Decoded_field))
  -> list(Decoded_field) = match left {
  [||] => right,
  field :: tail => field :: append_decoded_fields(tail, right),
}

function extracted_ea_byte_field(bytes : list(bits(8)), cursor : int, descriptor_bytes : int,
                                  positions : list(nat), accumulated : int) -> int =
  match positions {
    [||] => accumulated,
    position :: tail => match byte_at(bytes, cursor + descriptor_bytes - 1 - tdiv_int(position, 8)) {
      Some(byte) => extracted_ea_byte_field(bytes, cursor, descriptor_bytes, tail,
        accumulated * 2 + (if byte[unsigned(get_slice_int(3, position, 0))] == bitone then 1 else 0)),
      None() => { assert(false, "selected EA descriptor is incomplete"); 0 },
    },
  }

function decode_ea_byte_fields(bytes : list(bits(8)), cursor : int, descriptor_bytes : int,
                                fields : list(Ea_field)) -> list(Decoded_field) = match fields {
  [||] => [||],
  field :: tail => struct {
    symbol = field.symbol, operand_type = field.operand_type, kind = FieldBits,
    role = Some(field.role),
    value = extracted_ea_byte_field(bytes, cursor, descriptor_bytes, field.positions, 0)
  } :: decode_ea_byte_fields(bytes, cursor, descriptor_bytes, tail),
}

function decode_ea_fields(bytes : list(bits(8)), cursor : int, form : Ea_form) -> list(Decoded_field) =
  decode_ea_byte_fields(bytes, cursor, ea_pattern_count(form.patterns), form.fields)

function decode_ea_field_list(raw : bits(64), fields : list(Ea_field)) -> list(Decoded_field) =
  match fields {
    [||] => [||],
    field :: tail => struct {
      symbol = field.symbol,
      operand_type = field.operand_type,
      kind = FieldBits,
      role = Some(field.role),
      value = extracted_ea_field(raw, field.positions, 0)
    } :: decode_ea_field_list(raw, tail),
  }

function extracted_ea_field(payload : bits(64), positions : list(nat), accumulated : int) -> int =
  match positions {
    [||] => accumulated,
    position :: tail => extracted_ea_field(
      payload, tail, accumulated * 2 + (if sail_shiftright(payload, position)[0] == bitone then 1 else 0)
    ),
  }

function composed_ea_autoupdates(compact : Ea_form, descriptor : option(Ea_form)) -> list(Ea_autoupdate) = {
  let tail : list(Ea_autoupdate) = match descriptor {
    Some(form) => match form.autoupdate {
      Some(update) => [|update|], None() => [||],
    },
    None() => [||],
  };
  match compact.autoupdate {
    Some(update) => update :: tail, None() => tail,
  }
}

function parse_one_ea_descriptor(operand_name : Operand_id, operand_width : option(Ea_width), profile : Ea_profile,
                                 raw_value : int, bytes : list(bits(8)), cursor : int)
  -> option((Ea_binding, int)) =
  match find_compact_ea(profile, get_slice_int(16, raw_value, 0), effective_address_catalog()) {
    None() => None(),
    Some(compact) => {
      let descriptor_cursor = cursor;
      let descriptor_bytes = compact.descriptor_bytes;
      let descriptor : option(Ea_form) = if descriptor_bytes > 0 then match compact.descriptor {
        Some(family) => find_extended_ea(profile, family, bytes, descriptor_cursor,
                                         effective_address_catalog()),
        None() => None(),
      } else None();
      let descriptor_valid = if descriptor_bytes == 0 then true else match descriptor {
        Some(_) => true,
        None() => false,
      };
      if descriptor_bytes < 0 | not_bool(descriptor_valid) then None()
      else {
        let descriptor_name : option(Ea_form_id) = match descriptor {
          Some(form) => Some(form.name), None() => None()
        };
        let descriptor_fields : list(Decoded_field) = match descriptor {
          Some(form) => decode_ea_fields(bytes, descriptor_cursor, form),
          None() => [||],
        };
        let resolved_kind : Ea_kind = match descriptor {
          Some(form) => form.kind, None() => compact.kind
        };
        let resolved_segment : option(Ea_segment) = match descriptor {
          Some(form) => form.segment, None() => compact.segment
        };
        let resolved_base : option(Ea_base) = match descriptor {
          Some(form) => form.base, None() => compact.base
        };
        let compact_fields = decode_ea_field_list(
          sail_zero_extend(get_slice_int(16, raw_value, 0), 64), compact.fields
        );
        let resolved_expression : option(Ea_expression) = match descriptor {
          Some(form) => form.expression, None() => compact.expression
        };
        match resolved_expression {
        None() => None(),
        Some(expression) => Some((struct {
          operand_name = operand_name,
          ea = struct {
            compact_name = compact.name,
            profile = profile,
            kind = resolved_kind,
            segment = resolved_segment,
            base = resolved_base,
            expression = expression,
            operand_width = operand_width,
            descriptor_name = descriptor_name,
            raw = raw_value,
            descriptor_bytes = descriptor_bytes,
            fields = append_decoded_fields(compact_fields, descriptor_fields),
            payload_layout = append_ea_payload_layout(compact.payloads, match descriptor {
              Some(form) => form.payloads, None() => [||]
            }),
            payloads = [||],
            autoupdates = composed_ea_autoupdates(compact, descriptor)
          }
        }, cursor + descriptor_bytes)),
        }
      }
    },
  }

function parse_ea_descriptors(operands : list(Catalog_operand), fields : list(Decoded_field),
                              bytes : list(bits(8)), cursor : int) -> option(Ea_parse) =
  match operands {
    [||] => Some(struct { bindings = [||], cursor = cursor }),
    operand :: tail =>
      match (operand.ea_profile, operand.field_symbol) {
        (Some(profile), Some(symbol)) => match find_decoded_field(symbol, fields) {
          None() => None(),
          Some(field) => match parse_one_ea_descriptor(
            operand.name, operand.ea_width, profile, field.value, bytes, cursor
          ) {
            None() => None(),
            Some((binding, next_cursor)) => match parse_ea_descriptors(tail, fields, bytes, next_cursor) {
              None() => None(),
              Some(parsed) => Some(struct { bindings = binding :: parsed.bindings, cursor = parsed.cursor }),
            },
          },
        },
        _ => parse_ea_descriptors(tail, fields, bytes, cursor),
      },
  }

function ea_failure_stage(operands : list(Catalog_operand), fields : list(Decoded_field),
                          bytes : list(bits(8)), cursor : int) -> Decode_stage = match operands {
  [||] => DecodeSuccess,
  operand :: tail =>
    match (operand.ea_profile, operand.field_symbol) {
    (Some(profile), Some(symbol)) => match find_decoded_field(symbol, fields) {
      None() => DecodeStaticLegality,
      Some(field) => match find_compact_ea(
        profile, get_slice_int(16, field.value, 0), effective_address_catalog()
      ) {
        None() => DecodeEaDescriptor,
        Some(compact) => {
          let descriptor_bytes = compact.descriptor_bytes;
          if descriptor_bytes < 0 | byte_count(bytes) < cursor + descriptor_bytes then DecodeEaDescriptor
          else {
            let descriptor : option(Ea_form) = if descriptor_bytes > 0 then match compact.descriptor {
              Some(family) => find_extended_ea(profile, family, bytes, cursor,
                                                effective_address_catalog()),
              None() => None(),
            } else None();
            let descriptor_valid = if descriptor_bytes == 0 then true else match descriptor {
              Some(_) => true,
              None() => false,
            };
            if not_bool(descriptor_valid) then DecodeEaDescriptor
            else {
              ea_failure_stage(tail, fields, bytes, cursor + descriptor_bytes)
            }
          }
        },
      },
    },
    _ => ea_failure_stage(tail, fields, bytes, cursor),
    }
}

function find_ea_binding(name : Operand_id, bindings : list(Ea_binding)) -> option(Decoded_ea) = match bindings {
  [||] => None(),
  binding :: tail => if binding.operand_name == name then Some(binding.ea) else find_ea_binding(name, tail),
}

function find_payload(name : Operand_id, payloads : list(Catalog_payload)) -> option(Catalog_payload) =
  match payloads {
    [||] => None(),
    payload :: tail => if payload.operand_name == name then Some(payload) else find_payload(name, tail),
  }
