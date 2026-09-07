function stack_request(kind : Primitive_request_kind, ordinal : int, state : Cpu_state,
                       address : bits(64), range_end : bits(64), access : Transaction_access,
                       value : bits(64), commit_point : bool) -> Primitive_request = {
  let point = segment_point(state.segments[2], address);
  struct {
    kind = kind, ordinal = ordinal, operand = 255,
    role = RequestRoleStack, domain = RequestDomainStack, segment = 2,
    segment_image = state.segments[2], width = 8, range_start = address, range_end = range_end,
    range_length = unsigned(sub_bits(range_end, address)),
    range_wrap = unsigned(range_end) < unsigned(address) & range_end != 0x0000000000000000,
    range_end_at_modulus = range_end == 0x0000000000000000
                           & address != 0x0000000000000000,
    effective_address = address, linear_address = point.linear, access = access,
    value = if kind == Write then 0x0000000000000000 else value,
    address_translation = true, debug_validation = false, debug_validated = false,
    physical_address = 0x0000000000000000,
    memory_ranges = [|struct {
      effective_address = address, linear_address = point.linear,
      width = unsigned(sub_bits(range_end, address)), buffer_offset = 0
    }|],
    validation_ranges = [|struct {
      effective_address = address, linear_address = point.linear,
      width = unsigned(sub_bits(range_end, address)),
    }|],
    read_completion = ReadCompletionData, memory_cache_hint = MemoryCacheDefault,
    commit_point = commit_point, memory_order = ordinal,
    cache_policy = 0, suppress_fault = false, staged_updates = [||]
    , selector = 0x0000000000000000, expected = 0x0000000000000000,
    desired = 0x0000000000000000, auxiliary = 0x0000000000000000,
    body_length = if access == AccessStackRead
                  then unsigned(sub_bits(range_end, address)) else 0,
    payload_bytes = if kind == Write
                    then memory_value_bytes(value, unsigned(sub_bits(range_end, address)))
                    else [||]
  }
}

function compound_stack_store(ordinal : int, state : Cpu_state, low_address : bits(64),
                              low_value : bits(64), high_value : bits(64))
  -> Primitive_request = {
  let point = segment_point(state.segments[2], low_address);
  { stack_request(Write, ordinal, state, low_address,
                  add_bits(low_address, 0x0000000000000010), AccessStackWrite,
                  low_value, true) with
    linear_address = point.linear, width = 16, range_length = 16,
    payload_bytes = append_bytes(
      memory_value_bytes(low_value, 8), memory_value_bytes(high_value, 8)) }
}

function pair_first_index(pair_id : int) -> int = pair_id * 2
