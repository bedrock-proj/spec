enum Metadata_field_kind = FieldRn | FieldFreg | FieldEa | FieldCondition | FieldSize | FieldImmediate | FieldBits
enum Metadata_access = AccessRead | AccessWrite | AccessReadWrite | AccessAddress

struct Catalog_range = {
  lower : int,
  upper : int,
}

struct Catalog_constraint = {
  field_positions : list(range(0, 63)),
  ranges : list(Catalog_range),
  reason : string,
}

struct Catalog_field = {
  symbol : Field_id,
  operand_type : Operand_type,
  kind : Metadata_field_kind,
  positions : list(range(0, 63)),
}

struct Catalog_operand = {
  name : Operand_id,
  operand_type : Operand_type,
  access : Metadata_access,
  field_symbol : option(Field_id),
  field_positions : list(range(0, 63)),
  domain : option(Operand_domain),
  ea_role : option(Ea_role),
  ea_width : option(Ea_width),
  ea_profile : option(Ea_profile),
  has_fixed_value : bool,
  fixed_value : int,
  legal_values : list(int),
}

struct Catalog_payload = {
  operand_name : Operand_id,
  operand_type : Operand_type,
  width : int,
  signed : bool,
}

struct Ea_pattern = {
  width : int,
  value : bits(16),
  mask : bits(16),
}

struct Ea_field = {
  symbol : Field_id,
  operand_type : Operand_type,
  role : Ea_role,
  positions : list(nat),
}

enum Ea_payload_role = EaPayloadRole_displacement | EaPayloadRole_absolute | EaPayloadRole_immediate

struct Ea_payload = {
  role : Ea_payload_role,
  width : int,
  signed : bool,
}

function ea_payload_bits(payloads : list(Ea_payload)) -> int = match payloads {
  [||] => 0,
  payload :: tail => payload.width + ea_payload_bits(tail),
}

function append_ea_payload_layout(left : list(Ea_payload), right : list(Ea_payload)) -> list(Ea_payload) = match left {
  [||] => right,
  payload :: tail => payload :: append_ea_payload_layout(tail, right),
}

union Ea_expression_step = {
  EaExprLiteral : bits(64),
  EaExprField : Ea_role,
  EaExprPayload : Ea_payload_role,
  EaExprScale : Ea_update_difference,
  EaExprGpr : bits(4),
  EaExprSp : unit,
  EaExprPc : unit,
  EaExprNegate : unit,
  EaExprAdd : unit,
  EaExprSubtract : unit,
  EaExprMultiply : unit,
}

type Ea_expression = list(Ea_expression_step)

struct Ea_autoupdate = {
  target : Ea_update_target,
  mode : Ea_update_mode,
  difference : Ea_update_difference,
}

struct Ea_form = {
  name : Ea_form_id,
  profile : option(Ea_profile),
  descriptor_family : option(Ea_descriptor_family),
  descriptor_bytes : int,
  patterns : list(Ea_pattern),
  kind : Ea_kind,
  fields : list(Ea_field),
  segment : option(Ea_segment),
  payloads : list(Ea_payload),
  base : option(Ea_base),
  expression : option(Ea_expression),
  descriptor : option(Ea_descriptor_family),
  autoupdate : option(Ea_autoupdate),
}

struct Representative_record = {
  form_id : Form_id,
  bytes : list(bits(8)),
}

struct Catalog_entry = {
  form_id : Form_id,
  operation : Semantic_operation,
  route : Semantic_route,
  instruction_set : Instruction_set,
  privilege : Privilege_level,
  predicate_mode : Predicate_mode,
  repeat_rep : bool,
  repeat_repcc : bool,
  encoding_class : Encoding_class,
  value : bits(64),
  mask : bits(64),
  constraints : list(Catalog_constraint),
  fields : list(Catalog_field),
  operands : list(Catalog_operand),
  sizes : list((int, Size_code)),
  appended_payloads : list(Catalog_payload),
  required_cpuid_flags : list(Cpuid_flag),
}

function size_codes(items : list((int, Size_code))) -> list(Size_code) = match items {
  [||] => [||],
  (_, code) :: tail => code :: size_codes(tail),
}
