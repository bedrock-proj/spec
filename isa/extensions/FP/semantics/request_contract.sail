function fp_operation_is_exact_local(operation : Semantic_operation) -> bool =
  match fp_semantics(operation) {
    Some(semantics) => semantics.exact_operation != None(),
    None() => false,
  }

function fp_operand_image_valid(operand : Fp_operand_image) -> bool =
  if operand.valid then match operand.kind {
    FpValueNone => false,
    FpBits16 => operand.bits[63 .. 16] == 0x000000000000,
    FpBits32 => operand.bits[63 .. 32] == 0x00000000,
    _ => true,
  }
  else operand.kind == FpValueNone & operand.bits == 0x0000000000000000

function fp_operand_slot_get(slots : Fp_operand_slots,
                             index : int) -> option(Fp_operand_image) =
  if index == 0 then Some(slots.operand0)
  else if index == 1 then Some(slots.operand1)
  else if index == 2 then Some(slots.operand2)
  else None()

function fp_operand_slot_set(slots : Fp_operand_slots, index : int,
                             operand : Fp_operand_image) -> option(Fp_operand_slots) =
  if index == 0 then Some({ slots with operand0 = operand })
  else if index == 1 then Some({ slots with operand1 = operand })
  else if index == 2 then Some({ slots with operand2 = operand })
  else None()

function fp_operand_valid_bitmap(slots : Fp_operand_slots) -> bits(3) =
  (if slots.operand2.valid then 0b1 else 0b0)
  @ (if slots.operand1.valid then 0b1 else 0b0)
  @ (if slots.operand0.valid then 0b1 else 0b0)

function fp_operand_count_bitmap(count : int) -> option(bits(3)) =
  if count == 0 then Some(0b000)
  else if count == 1 then Some(0b001)
  else if count == 2 then Some(0b011)
  else if count == 3 then Some(0b111)
  else None()

function fp_operand_slots_valid(slots : Fp_operand_slots, count : int) -> bool =
  fp_operand_image_valid(slots.operand0)
  & fp_operand_image_valid(slots.operand1)
  & fp_operand_image_valid(slots.operand2)
  & (match fp_operand_count_bitmap(count) {
      Some(expected) => fp_operand_valid_bitmap(slots) == expected,
      None() => false,
    })

function fp_operand_slot_valid(operand : Fp_operand_image, used : bool) -> bool =
  fp_operand_image_valid(operand) & operand.valid == used

function fp_request_arity_valid(request : Fp_request_image) -> bool =
  request.operand_count >= 0 & request.operand_count <= 3
  & fp_operand_slot_valid(request.operand0, request.operand_count >= 1)
  & fp_operand_slot_valid(request.operand1, request.operand_count >= 2)
  & fp_operand_slot_valid(request.operand2, request.operand_count >= 3)

function fp_result_kind_width(kind : Fp_result_kind) -> int = match kind {
  FpResultBits16 => 2,
  FpResultBits32 => 4,
  FpResultPair32 => 4,
  FpResultBits64 => 8,
  FpResultRaw64 => 8,
  FpResultPair64 => 8,
  FpResultPairRaw64 => 8,
  _ => 0,
}

function fp_result_kind_is_pair(kind : Fp_result_kind) -> bool =
  kind == FpResultPair32 | kind == FpResultPair64 | kind == FpResultPairRaw64

function fp_result_kind_is_float(kind : Fp_result_kind) -> bool =
  kind == FpResultBits16 | kind == FpResultBits32 | kind == FpResultBits64
  | kind == FpResultPair32 | kind == FpResultPair64

function fp_fn_write_count(operands : list(Catalog_operand)) -> int = match operands {
  [||] => 0,
  operand :: tail =>
    (if operand.operand_type == OperandType_Fn
        & (operand.access == AccessWrite | operand.access == AccessReadWrite)
     then 1 else 0)
    + fp_fn_write_count(tail),
}

function fp_policy_result_kind(policy : Fp_result_policy,
                               raw_result : bool) -> option(Fp_result_kind) =
  if raw_result then Some(FpResultPairRaw64)
  else match policy {
    FpResultNone => Some(FpResultValueNone),
    FpResultFn16 => Some(FpResultBits16),
    FpResultMemory16 => Some(FpResultBits16),
    FpResultFn32 => Some(FpResultBits32),
    FpResultMemory32 => Some(FpResultBits32),
    FpResultFn64 => Some(FpResultBits64),
    FpResultMemory64 => Some(FpResultBits64),
    FpResultRn64 => Some(FpResultInteger),
    FpResultFlags => Some(FpResultValueFlags),
    FpResultFlagsVOnly => Some(FpResultValueFlags),
    FpResultFnPair32 => Some(FpResultPair32),
    FpResultFnPair64 => Some(FpResultPair64),
    _ => None(),
  }

function fp_derive_result_kind(form : Catalog_entry,
                               width : int) -> option(Fp_result_kind) =
  match (fp_form_result_policy(form, width), fp_semantics(form.operation)) {
    (Some(policy), Some(semantics)) => fp_policy_result_kind(policy, semantics.raw_result),
    _ => None(),
  }

function fp_derive_destination_kind(form : Catalog_entry) -> Fp_destination_kind =
  match fp_semantics(form.operation) {
    Some(semantics) =>
      if semantics.result_policy == FpResultFnPairByFormat
         | semantics.result_policy == FpResultFnPair32
         | semantics.result_policy == FpResultFnPair64
      then FpDestinationFnPair
      else if semantics.result_policy == FpResultMemory32
              | semantics.result_policy == FpResultMemory16
              | semantics.result_policy == FpResultMemory64
              | fp_has_ea_write(form.operands)
      then FpDestinationMemory
      else if fp_has_rn_write(form.operands) then FpDestinationRn
      else
      if semantics.result_policy == FpResultFlags
         | semantics.result_policy == FpResultFlagsVOnly
      then FpDestinationFlags
      else if fp_fn_write_count(form.operands) > 0 then FpDestinationFn
      else FpDestinationNone,
    None() => FpDestinationNone,
  }

function fp_request_width(request : Fp_request_image) -> int = {
  request.operation_width
}

function fp_operation_has_intrinsic_conversion(operation : Semantic_operation) -> bool =
  match fp_semantics(operation) {
    Some(semantics) => semantics.conversion_unsigned != None(),
    None() => false,
  }

function fp_operand_needs_format_conversion(operation : Semantic_operation,
                                            operation_width : int,
                                            operand : Fp_operand_image) -> bool =
  not_bool(fp_operation_has_intrinsic_conversion(operation))
  & operand.valid & fp_operand_width(operand) != 0
  & fp_operand_width(operand) != operation_width

function fp_request_needs_format_conversion(request : Fp_request_image) -> bool =
  fp_operand_needs_format_conversion(request.operation, request.operation_width,
                                     request.operand0)
  | fp_operand_needs_format_conversion(request.operation, request.operation_width,
                                       request.operand1)
  | fp_operand_needs_format_conversion(request.operation, request.operation_width,
                                       request.operand2)

function fp_request_is_exact_local(request : Fp_request_image) -> bool =
  fp_operation_is_exact_local(request.operation)
  & not_bool(fp_request_needs_format_conversion(request))

function fp_request_conversion_allowed_causes(request : Fp_request_image) -> bits(5) =
  if fp_request_needs_format_conversion(request)
  then fflags_nv | fflags_of | fflags_uf | fflags_nx
  else fflags_none

function fp_result_policy_matches(kind : Fp_result_kind, policy : Fp_result_policy,
                                  raw_result : bool) -> bool =
  if raw_result then kind == FpResultPairRaw64
  else match policy {
    FpResultNone => kind == FpResultValueNone,
    FpResultFn16 => kind == FpResultBits16,
    FpResultMemory16 => kind == FpResultBits16,
    FpResultFn32 => kind == FpResultBits32,
    FpResultMemory32 => kind == FpResultBits32,
    FpResultFn64 => kind == FpResultBits64,
    FpResultMemory64 => kind == FpResultBits64,
    FpResultRn64 => kind == FpResultInteger,
    FpResultFlags => kind == FpResultValueFlags,
    FpResultFlagsVOnly => kind == FpResultValueFlags,
    FpResultFnPair32 => kind == FpResultPair32,
    FpResultFnPair64 => kind == FpResultPair64,
    _ => false,
  }

function fp_find_form(forms : list(Catalog_entry), form_id : Form_id) -> option(Catalog_entry) =
  match forms {
    [||] => None(),
    form :: tail => if form.form_id == form_id then Some(form) else fp_find_form(tail, form_id),
  }

function fp_find_primary_form(form_id : Form_id) -> option(Catalog_entry) =
  match fp_find_form(primary_form_catalog_for(ExtraShort), form_id) {
    Some(form) => Some(form),
    None() => match fp_find_form(primary_form_catalog_for(Short), form_id) {
      Some(form) => Some(form),
      None() => match fp_find_form(primary_form_catalog_for(Medium), form_id) {
        Some(form) => Some(form),
        None() => match fp_find_form(primary_form_catalog_for(Long), form_id) {
          Some(form) => Some(form),
          None() => match fp_find_form(primary_form_catalog_for(ExtraLong), form_id) {
            Some(form) => Some(form),
            None() => fp_find_form(primary_form_catalog_for(Xxlong), form_id),
          },
        },
      },
    },
  }

function fp_request_contract_valid(request : Fp_request_image,
                                   semantics : Fp_semantics) -> bool =
  if semantics.transcendental then
    request.contract_id == semantics.contract_id
    & fp_approx_contract_operation(request.contract_id) == Some(request.operation)
    & fp_approx_contract_word_valid(request.contract_word)
    & (request.allowed_causes & fflags_nx)
      == (fp_request_conversion_allowed_causes(request) & fflags_nx)
  else request.contract_id == 0x0000
       & request.contract_word == 0x0000000000000000

function fp_request_form_valid(request : Fp_request_image) -> bool =
  match fp_find_primary_form(request.form_id) {
    None() => false,
    Some(form) => {
      let width = fp_request_width(request);
      form.operation == request.operation
      & (match fp_form_semantics(form, width) {
          Some(form_semantics) =>
            request.path == form_semantics.path
            & fp_result_policy_matches(request.result_kind,
                                       form_semantics.result_policy,
                                       form_semantics.raw_result),
          None() => false,
        })
    },
  }

function fp_request_valid(request : Fp_request_image) -> bool =
  request.valid & request.form_id != Form_invalid
  & (request.operation_width == 1 | request.operation_width == 2
     | request.operation_width == 4 | request.operation_width == 8)
  & fp_request_arity_valid(request)
  & (match fp_semantics(request.operation) {
      None() => false,
      Some(semantics) =>
        request.operand_count == semantics.source_count
        & request.allowed_causes
          == (semantics.allowed_causes | fp_request_conversion_allowed_causes(request))
        & request.flags_mask == semantics.flags_mask
        & request.transcendental == semantics.transcendental
        & fp_request_contract_valid(request, semantics)
        & fp_request_form_valid(request),
    })

function fp_prepare_request_operand(operation : Semantic_operation,
                                    operand : Fp_operand_image,
                                    operation_width : int,
                                    daz : bool) -> Fp_operand_image =
  if fp_operation_is_exact_local(operation)
     & not_bool(fp_operand_needs_format_conversion(operation, operation_width,
                                                   operand))
  then operand
  else fp_apply_daz(operand, daz)

function fp_build_request(operation : Semantic_operation, form : Catalog_entry,
                          path : Fp_path, result_kind : Fp_result_kind,
                          operation_width : int,
                          operands : Fp_operand_slots, fstatus : bits(64),
                          contract_word : bits(64)) -> option(Fp_request_image) =
  match fp_semantics(operation) {
    None() => None(),
    Some(semantics) => {
      let daz = fp_fstatus_daz(fstatus);
      let prepared : Fp_operand_slots = struct {
        operand0 = fp_prepare_request_operand(
          operation, operands.operand0, operation_width, daz
        ),
        operand1 = fp_prepare_request_operand(
          operation, operands.operand1, operation_width, daz
        ),
        operand2 = fp_prepare_request_operand(
          operation, operands.operand2, operation_width, daz
        )
      };
      let base_request : Fp_request_image = struct {
        valid = true,
        operation = operation,
        form_id = form.form_id,
        path = path,
        operation_width = operation_width,
        result_kind = result_kind,
        operand_count = semantics.source_count,
        operand0 = prepared.operand0,
        operand1 = prepared.operand1,
        operand2 = prepared.operand2,
        rounding_mode = if semantics.transcendental then 0b00
                        else fp_fstatus_rounding_mode(fstatus),
        daz = daz,
        ftz = fp_fstatus_ftz(fstatus),
        dn = fp_fstatus_dn(fstatus),
        allowed_causes = fflags_none,
        flags_mask = semantics.flags_mask,
        transcendental = semantics.transcendental,
        contract_id = semantics.contract_id,
        contract_word = contract_word
      };
      let request = { base_request with
        allowed_causes = semantics.allowed_causes
                         | fp_request_conversion_allowed_causes(base_request) };
      let metadata_valid = form.operation == operation
                           & fp_form_path(form) == Some(path)
                           & fp_derive_result_kind(form, operation_width)
                             == Some(result_kind);
      if fp_fstatus_valid(fstatus)
         & fp_operand_slots_valid(operands, semantics.source_count)
         & metadata_valid & fp_request_valid(request)
      then Some(request) else None()
    },
  }
