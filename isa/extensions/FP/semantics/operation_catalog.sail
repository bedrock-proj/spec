enum Fp_path = FpPathR | FpPathS1 | FpPathS2 | FpPathD1 | FpPathStack

enum Fp_result_policy =
  FpResultNone
| FpResultFnByFormat
| FpResultByForm
| FpResultFn16
| FpResultFn32
| FpResultFn64
| FpResultRn64
| FpResultFlags
| FpResultFlagsVOnly
| FpResultFnPairByFormat
| FpResultFnPair32
| FpResultFnPair64
| FpResultMemory16
| FpResultMemory32
| FpResultMemory64

enum Fp_exact_operation =
  FpExactAbsolute
| FpExactNegate
| FpExactCopySign
| FpExactMove
| FpExactClear
| FpExactExchange
| FpExactClassify
| FpExactMoveConstant

struct Fp_semantics = {
  path : Fp_path,
  source_count : int,
  result_policy : Fp_result_policy,
  allowed_causes : bits(5),
  flags_mask : bits(4),
  transcendental : bool,
  contract_id : bits(16),
  exact_operation : option(Fp_exact_operation),
  raw_result : bool,
  conversion_unsigned : option(bool),
  conversion_fn_width : int,
}

let fflags_nv : bits(5) = 0b10000
let fflags_dz : bits(5) = 0b01000
let fflags_of : bits(5) = 0b00100
let fflags_uf : bits(5) = 0b00010
let fflags_nx : bits(5) = 0b00001
let fflags_none : bits(5) = 0b00000
let fflags_all : bits(5) = 0b11111
let fp_flags_none : bits(4) = 0b0000
let fp_flags_all : bits(4) = 0b1111

function fp_info(path : Fp_path, source_count : int, result_policy : Fp_result_policy,
                 allowed_causes : bits(5), flags_mask : bits(4),
                 transcendental : bool, contract_id : int) -> Fp_semantics = struct {
  path = path,
  source_count = source_count,
  result_policy = result_policy,
  allowed_causes = allowed_causes,
  flags_mask = flags_mask,
  transcendental = transcendental,
  contract_id = get_slice_int(16, contract_id, 0),
  exact_operation = None(),
  raw_result = false,
  conversion_unsigned = None(),
  conversion_fn_width = 0
}

function fp_exact_info(info : Fp_semantics, operation : Fp_exact_operation) -> Fp_semantics =
  { info with exact_operation = Some(operation) }

function fp_raw_result_info(info : Fp_semantics) -> Fp_semantics =
  { info with raw_result = true }

function fp_conversion_info(info : Fp_semantics, unsigned_conversion : bool,
                            fn_width : int) -> Fp_semantics =
  { info with conversion_unsigned = Some(unsigned_conversion), conversion_fn_width = fn_width }

val fp_semantics : Semantic_operation -> option(Fp_semantics)

scattered function fp_semantics

function fp_causes_valid(semantics : Fp_semantics, generated : bits(5)) -> bool =
  (generated & semantics.allowed_causes) == generated

function fp_operation_causes_valid(operation : Semantic_operation,
                                   generated : bits(5)) -> bool =
  match fp_semantics(operation) {
    Some(semantics) => fp_causes_valid(semantics, generated),
    None() => false,
  }

function fp_enabled_causes(fstatus : bits(64), generated : bits(5)) -> bits(5) =
  fstatus[4 .. 0] & generated

function fp_traps(fstatus : bits(64), generated : bits(5)) -> bool =
  fp_enabled_causes(fstatus, generated) != fflags_none

function fp_ea_read_count(operands : list(Catalog_operand)) -> int = match operands {
  [||] => 0,
  operand :: tail =>
    (if operand.operand_type == OperandType_FEA & operand.access == AccessRead then 1 else 0)
    + fp_ea_read_count(tail),
}

function fp_has_ea_write(operands : list(Catalog_operand)) -> bool = match operands {
  [||] => false,
  operand :: tail =>
    (operand.operand_type == OperandType_FEA
     & (operand.access == AccessWrite | operand.access == AccessReadWrite))
    | fp_has_ea_write(tail),
}

function fp_has_rn_write(operands : list(Catalog_operand)) -> bool = match operands {
  [||] => false,
  operand :: tail =>
    (operand.operand_type == OperandType_Rn
     & (operand.access == AccessWrite | operand.access == AccessReadWrite))
    | fp_has_rn_write(tail),
}

function fp_form_path(form : Catalog_entry) -> option(Fp_path) =
  match fp_semantics(form.operation) {
    None() => None(),
    Some(semantics) =>
      if semantics.path == FpPathStack then Some(FpPathStack)
      else if fp_has_ea_write(form.operands) then Some(FpPathD1)
      else if fp_ea_read_count(form.operands) >= 2 then Some(FpPathS2)
      else if fp_ea_read_count(form.operands) == 1 then Some(FpPathS1)
      else Some(FpPathR),
  }

function fp_fn_result(width : int) -> Fp_result_policy =
  if width == 2 then FpResultFn16
  else if width == 4 then FpResultFn32 else FpResultFn64

function fp_pair_result(width : int) -> Fp_result_policy =
  if width == 4 then FpResultFnPair32 else FpResultFnPair64

function fp_memory_result(width : int) -> Fp_result_policy =
  if width == 2 then FpResultMemory16
  else if width == 4 then FpResultMemory32 else FpResultMemory64

function fp_conversion_fn_width(operation : Semantic_operation) -> int =
  match fp_semantics(operation) {
    Some(semantics) => semantics.conversion_fn_width,
    None() => 0,
  }

function fp_size_list_is_integer(items : list(Size_code)) -> bool = match items {
  Size_B :: _ => true,
  Size_W :: _ => true,
  Size_L :: _ => true,
  Size_Q :: _ => true,
  _ => false,
}

function fp_conversion_form_result_policy(form : Catalog_entry, width : int)
  -> option(Fp_result_policy) =
  if fp_has_rn_write(form.operands) then Some(FpResultRn64)
  else if fp_has_ea_write(form.operands) then
    if fp_size_list_is_integer(size_codes(form.sizes)) then Some(FpResultRn64)
    else Some(fp_memory_result(width))
  else Some(fp_fn_result(fp_conversion_fn_width(form.operation)))

function fp_form_result_policy(form : Catalog_entry, width : int) -> option(Fp_result_policy) =
  match fp_semantics(form.operation) {
    None() => None(),
    Some(semantics) =>
      if semantics.conversion_unsigned != None() then
        if width != 1 & width != 2 & width != 4 & width != 8 then None()
        else fp_conversion_form_result_policy(form, width)
      else if width != 4 & width != 8 then None()
      else if semantics.result_policy == FpResultFn32
           | semantics.result_policy == FpResultFn16
           | semantics.result_policy == FpResultFn64
           | semantics.result_policy == FpResultFnPair32
           | semantics.result_policy == FpResultFnPair64
           | semantics.result_policy == FpResultMemory16
           | semantics.result_policy == FpResultMemory32
           | semantics.result_policy == FpResultMemory64
        then Some(semantics.result_policy)
      else if semantics.result_policy == FpResultFnPairByFormat then Some(fp_pair_result(width))
      else if semantics.result_policy == FpResultFlags
           | semantics.result_policy == FpResultFlagsVOnly
           | semantics.result_policy == FpResultNone
           | semantics.result_policy == FpResultRn64
        then Some(semantics.result_policy)
      else if fp_has_rn_write(form.operands) then Some(FpResultRn64)
      else if fp_has_ea_write(form.operands) then Some(fp_memory_result(width))
      else Some(fp_fn_result(width)),
  }

function fp_form_semantics(form : Catalog_entry, width : int) -> option(Fp_semantics) =
  match (fp_semantics(form.operation), fp_form_path(form),
         fp_form_result_policy(form, width)) {
    (Some(semantics), Some(path), Some(result_policy)) =>
      Some({ semantics with path = path, result_policy = result_policy }),
    _ => None(),
  }

function fp_operation_count(operations : list(Semantic_operation)) -> int = match operations {
  [||] => 0,
  operation :: tail => {
    let selected : int = match fp_semantics(operation) { Some(_) => 1, None() => 0 };
    selected + fp_operation_count(tail)
  },
}

function fp_form_count(forms : list(Catalog_entry)) -> int = match forms {
  [||] => 0,
  form :: tail => {
    let selected : int = match fp_semantics(form.operation) { Some(_) => 1, None() => 0 };
    selected + fp_form_count(tail)
  },
}

function fp_path_count(forms : list(Catalog_entry), path : Fp_path) -> int = match forms {
  [||] => 0,
  form :: tail => {
    let selected : int = match fp_form_path(form) {
      Some(form_path) => if form_path == path then 1 else 0,
      None() => 0,
    };
    selected + fp_path_count(tail, path)
  },
}
