struct Decoded_field = {
  symbol : Field_id,
  operand_type : Operand_type,
  kind : Metadata_field_kind,
  role : option(Ea_role),
  value : int,
}

struct Decoded_ea_payload = {
  definition : Ea_payload,
  value : bits(64),
}

struct Decoded_payload = {
  operand_name : Operand_id,
  value : bits(64),
}

struct Decoded_ea = {
  compact_name : Ea_form_id,
  profile : Ea_profile,
  kind : Ea_kind,
  segment : option(Ea_segment),
  base : option(Ea_base),
  expression : Ea_expression,
  operand_width : option(Ea_width),
  descriptor_name : option(Ea_form_id),
  raw : int,
  descriptor_bytes : int,
  fields : list(Decoded_field),
  payload_layout : list(Ea_payload),
  payloads : list(Decoded_ea_payload),
  autoupdates : list(Ea_autoupdate),
}

struct Decoded_operand = {
  name : Operand_id,
  operand_type : Operand_type,
  access : Metadata_access,
  domain : option(Operand_domain),
  ea_role : option(Ea_role),
  ea_width : option(Ea_width),
  value : bits(64),
  ea : option(Decoded_ea),
}

struct Ea_binding = {
  operand_name : Operand_id,
  ea : Decoded_ea,
}

struct Ea_parse = {
  bindings : list(Ea_binding),
  cursor : int,
}

struct Operand_parse = {
  operands : list(Decoded_operand),
  cursor : int,
}

struct Payload_parse = {
  payloads : list(Decoded_payload),
  cursor : int,
}

struct Decoded_instruction = {
  form : Catalog_entry,
  encoding_bits : bits(64),
  fields : list(Decoded_field),
  operands : list(Decoded_operand),
  encoded_length : int,
  required_length : int,
  bytes : list(bits(8)),
}

struct Decode_outcome = {
  stage : Decode_stage,
  candidate_form : option(Catalog_entry),
  instruction : option(Decoded_instruction),
  detail : string,
}
