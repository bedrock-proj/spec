function extracted_field(encoding : bits(64), positions : list(range(0, 63)), accumulated : int) -> int =
  match positions {
    [||] => accumulated,
    position :: tail => extracted_field(
      encoding,
      tail,
      accumulated * 2 + (if encoding[position] == bitone then 1 else 0)
    ),
  }

function value_in_ranges(value : int, ranges : list(Catalog_range)) -> bool = match ranges {
  [||] => false,
  allowed :: tail =>
    (allowed.lower <= value & value <= allowed.upper) | value_in_ranges(value, tail),
}

function constraint_matches(encoding : bits(64), item : Catalog_constraint) -> bool = {
  let value = extracted_field(encoding, item.field_positions, 0);
  value_in_ranges(value, item.ranges)
}

function constraints_match(encoding : bits(64), constraints : list(Catalog_constraint)) -> bool =
  match constraints {
    [||] => true,
    item :: tail => constraint_matches(encoding, item) & constraints_match(encoding, tail),
  }

function catalog_entry_matches(encoding : bits(64), entry : Catalog_entry) -> bool =
  (encoding & entry.mask) == entry.value & constraints_match(encoding, entry.constraints)

function recognize_in_catalog(encoding : bits(64), encoding_class : Encoding_class,
                              entries : list(Catalog_entry)) -> option(Form_id) =
  match entries {
    [||] => None(),
    entry :: tail =>
      if entry.encoding_class == encoding_class & catalog_entry_matches(encoding, entry)
      then Some(entry.form_id)
      else recognize_in_catalog(encoding, encoding_class, tail),
  }

function recognize_primary_form(encoding : bits(64), encoding_class : Encoding_class) -> option(Form_id) =
  recognize_in_catalog(encoding, encoding_class, primary_form_catalog_for(encoding_class))

struct Framed_record = {
  record_class : Record_class,
  encoded_length : int,
}

function byte_count(bytes : list(bits(8))) -> int = match bytes {
  [||] => 0,
  _ :: tail => 1 + byte_count(tail),
}

function frame_record(bytes : list(bits(8))) -> option(Framed_record) = match bytes {
  [||] => None(),
  byte0 :: tail => {
    let second : option(bits(8)) = match tail {
      [||] => None(),
      byte1 :: _ => Some(byte1),
    };
    match framing_class(byte0, second) {
      None() => None(),
      Some(record_class) => {
        let actual = byte_count(bytes);
        let encoded = encoded_record_length(record_class, byte0);
        if actual == encoded & actual >= class_minimum(record_class)
        then Some(struct { record_class = record_class, encoded_length = encoded })
        else None()
      },
    }
  },
}

function required_bytes_sufficient(frame : Framed_record, required_bytes : int) -> bool =
  required_bytes >= class_minimum(frame.record_class)
  & required_bytes <= frame.encoded_length
  & required_bytes <= architecture_max_record_bytes

function zero_bytes(bytes : list(bits(8))) -> list(bits(8)) = match bytes {
  [||] => [||],
  _ :: tail => 0x00 :: zero_bytes(tail),
}

function zero_after(bytes : list(bits(8)), keep : int) -> list(bits(8)) = match bytes {
  [||] => [||],
  byte :: tail =>
    if keep > 0 then byte :: zero_after(tail, keep - 1)
    else 0x00 :: zero_bytes(tail),
}

function canonical_zero_padding(bytes : list(bits(8)), required_bytes : int) -> option(list(bits(8))) =
  match frame_record(bytes) {
    Some(frame) =>
      if required_bytes_sufficient(frame, required_bytes)
      then Some(zero_after(bytes, required_bytes))
      else None(),
    None() => None(),
  }
