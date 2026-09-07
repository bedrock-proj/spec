function byte_at(bytes : list(bits(8)), index : int) -> option(bits(8)) = match bytes {
  [||] => None(),
  byte :: tail => if index == 0 then Some(byte) else if index > 0 then byte_at(tail, index - 1) else None(),
}

function read_le_int(bytes : list(bits(8)), cursor : int, remaining : int,
                     factor : int, accumulated : int) -> option(int) =
  if remaining == 0 then Some(accumulated)
  else match byte_at(bytes, cursor) {
    Some(byte) => read_le_int(bytes, cursor + 1, remaining - 1,
                             factor * 256, accumulated + unsigned(byte) * factor),
    None() => None(),
  }

function read_le_bits(bytes : list(bits(8)), cursor : int, width : int) -> option(bits(64)) =
  if (width == 0) | (width == 8) | (width == 16) | (width == 24)
     | (width == 32) | (width == 40) | (width == 48) | (width == 56) | (width == 64) then
    match read_le_int(bytes, cursor, tdiv_int(width, 8), 1, 0) {
      Some(value) => Some(get_slice_int(64, value, 0)),
      None() => None(),
    }
  else None()

function read_be_int(bytes : list(bits(8)), cursor : int, remaining : int,
                     accumulated : int) -> option(int) =
  if remaining == 0 then Some(accumulated)
  else match byte_at(bytes, cursor) {
    Some(byte) => read_be_int(bytes, cursor + 1, remaining - 1,
                             accumulated * 256 + unsigned(byte)),
    None() => None(),
  }

function read_be_bits(bytes : list(bits(8)), cursor : int, width : int) -> option(bits(64)) =
  match read_be_int(bytes, cursor, tdiv_int(width, 8), 0) {
    Some(value) => Some(get_slice_int(64, value, 0)),
    None() => None(),
  }

function read_encoding_bits(bytes : list(bits(8)), encoding_class : Encoding_class) -> option(bits(64)) =
  match read_be_bits(bytes, 0, 8 * encoding_primary_bytes(encoding_class)) {
    Some(encoding) => Some(encoding & encoding_pattern_mask(encoding_class)),
    None() => None(),
  }

function find_catalog_entry(encoding : bits(64), encoding_class : Encoding_class,
                            entries : list(Catalog_entry)) -> option(Catalog_entry) =
  match entries {
    [||] => None(),
    entry :: tail =>
      if entry.encoding_class == encoding_class & catalog_entry_matches(encoding, entry)
      then Some(entry)
      else find_catalog_entry(encoding, encoding_class, tail),
  }

function find_opcode_entry(encoding : bits(64), encoding_class : Encoding_class,
                           entries : list(Catalog_entry)) -> option(Catalog_entry) =
  match entries {
    [||] => None(),
    entry :: tail =>
      if entry.encoding_class == encoding_class & ((encoding & entry.mask) == entry.value)
      then Some(entry)
      else find_opcode_entry(encoding, encoding_class, tail),
  }

function decode_fields(encoding : bits(64), fields : list(Catalog_field)) -> list(Decoded_field) =
  match fields {
    [||] => [||],
    field :: tail => struct {
      symbol = field.symbol,
      operand_type = field.operand_type,
      kind = field.kind,
      role = None(),
      value = extracted_field(encoding, field.positions, 0)
    } :: decode_fields(encoding, tail),
  }

function find_decoded_field(symbol : Field_id, fields : list(Decoded_field)) -> option(Decoded_field) =
  match fields {
    [||] => None(),
    field :: tail => if field.symbol == symbol then Some(field) else find_decoded_field(symbol, tail),
  }
