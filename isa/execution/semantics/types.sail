enum Boundary_class = { BoundaryOrdinary, BoundaryRepeatIteration, BoundaryVectorLane, BoundaryEventEntry }
struct Architectural_effect = {
  kind : Effect_kind,
  operation : Semantic_operation,
  form_id : Form_id,
  operands : list(Decoded_operand),
  pc : bits(64),
  status : bits(64),
  flags : bits(4),
  fstatus : bits(64),
  fflags : bits(5),
  address : bits(64),
  value : bits(64),
  width : int,
  order : int,
  suppress_fault : bool,
}

enum Primitive_request_kind = {
    NoPrimitiveRequest,
    TranslationExecuteProbe,
    MemoryProbe,
    Read,
    Write,
    StackRange,
    SegmentBoundsPoint,
    Atomic,
    AddressWake,
    AddressTranslateRequest,
    PhysicalPteRead,
    CacheMaintenanceBlock,
    PrefetchHint,
    FenceCompletion,
    TlbInvalidateRequest,
    TranslationQueryRequest,
    ContextSwitchRequest,
    RepeatBodyFetch,
    EventFrameAccess,
    CpuidQueryRequest,
    PerformanceCounterRequest,
    ControlTransitionRequest,
    ResetSerializeRequest,
    ExternalIntegerRequest,
}

enum Transaction_access = {
    NoTransactionAccess,
    AccessLoad,
    AccessStore,
    AccessReadModifyWrite,
    AccessExecute,
    AccessStackRead,
    AccessStackWrite,
    AccessAddressOnly,
}

enum Memory_access_class = { NormalAccess, MmioAccess }
enum Physical_memory_class = { NormalPhysical, DevicePhysical }
enum Read_completion = { ReadCompletionData, ReadCompletionWait }
enum Memory_cache_hint = { MemoryCacheDefault, MemoryCacheNonTemporal }

struct Memory_access_range = {
  effective_address : bits(64),
  linear_address : bits(64),
  width : int,
  buffer_offset : int,
}

struct Memory_validation_range = {
  effective_address : bits(64),
  linear_address : bits(64),
  width : int,
}

function memory_validation_ranges(ranges : list(Memory_access_range))
  -> list(Memory_validation_range) = match ranges {
  [||] => [||],
  range :: tail => struct {
    effective_address = range.effective_address,
    linear_address = range.linear_address,
    width = range.width,
  } :: memory_validation_ranges(tail),
}

enum Debug_stop_kind = {
    DebugStopExecution,
    DebugStopMemoryRead,
    DebugStopMemoryWrite,
    DebugStopAtomicMemory,
}

struct Debug_stop = {
  kind : Debug_stop_kind,
  slot : int,
  operand : int,
  access_size : int,
  effective_address : bits(64),
  linear_address : bits(64),
  linear_valid : bool,
  user_domain : bool,
  access_class : Memory_access_class,
}

struct Debug_trigger_match = {
  slot : int,
  accessed : Memory_access_range,
}

enum Continuation_phase = {
    ContinuationNone,
    PhaseSourceAddress,
    PhaseSourceRead,
    PhaseDestinationAddress,
    PhaseDestinationProbe,
    PhaseDestinationWrite,
    PhaseMemoryWriteFirst,
    PhaseMemoryWriteSecond,
    PhaseTargetRead,
    PhaseTargetExecuteProbe,
    PhaseStackRange,
    PhaseStackReadFirst,
    PhaseStackReadSecond,
    PhaseStackWriteFirst,
    PhaseIntegerExternal,
    PhaseCacheTranslate,
    PhaseCacheMaintenance,
    PhaseFenceCompletion,
    PhaseTlbOperation,
    PhaseTranslationQuery,
    PhasePteRead,
    PhaseContextSwitch,
    PhaseRepeatFetch,
    PhaseEventFrameHeader,
    PhaseEventFrame,
    PhaseEventTargetProbe,
    PhaseEventStackProbe,
    PhaseEventFrameStore,
    PhaseEventReturnExecute,
    PhaseCpuidQuery,
    PhasePerformanceCounter,
    PhaseControlTransition,
    PhaseResetSerialize,
}

enum Transaction_response_kind = {
    ResponseTranslation,
    ResponseProbe,
    ResponseRead,
    ResponseWrite,
    ResponseStackRange,
    ResponseSegmentBounds,
    ResponseIntegerExternal,
    ResponseAtomic,
    ResponseAddressWake,
    ResponseAddressTranslation,
    ResponsePteRead,
    ResponseCacheMaintenance,
    ResponseFenceCompletion,
    ResponseTlbOperation,
    ResponseTranslationQuery,
    ResponseContextSwitch,
    ResponseRepeatFetch,
    ResponseEventFrame,
    ResponseCpuidQuery,
    ResponsePerformanceCounter,
    ResponseControlTransition,
    ResponseResetSerialize,
}

struct Staged_register_update = {
  index : bits(4),
  value : bits(64),
}

struct Memory_write = {
  linear_address : bits(64),
  effective_address : bits(64),
  segment : int,
  width : int,
  value : bits(64),
  slot : int,
}

enum Request_domain = {
    RequestDomainCurrent,
    RequestDomainUser,
    RequestDomainCode,
    RequestDomainStack,
    RequestDomainPhysicalPageTable,
}

enum Request_role = {
    RequestRoleNone,
    RequestRoleValue,
    RequestRoleAddress,
    RequestRoleControlTarget,
    RequestRoleSingle,
    RequestRoleStack,
    RequestRoleEventEntryTarget,
    RequestRoleEventFrameRange,
    RequestRoleEventFrameStore,
    RequestRoleUserReturnTarget,
    RequestRoleUserBankTarget,
    RequestRoleCacheTranslate,
    RequestRoleFpApproximationContractQuery,
    RequestRolePtqueryPte,
    RequestRoleVtopPte,
    RequestRolePhysicalPrefetch,
    RequestRolePhysicalCacheBlock,
}

enum Commit_destination_kind = { DestinationNone, DestinationRn, DestinationFn, DestinationSP }

struct Ea_evaluation = {
  operand_name : Operand_id,
  kind : Ea_kind,
  role : option(Ea_role),
  domain : option(Operand_domain),
  segment : int,
  segment_image : bits(64),
  width : int,
  effective_address : bits(64),
  linear_address : bits(64),
  value : bits(64),
  staged_updates : list(Staged_register_update),
}

struct Ea_evaluation_set = {
  evaluations : list(Ea_evaluation),
  image : Cpu_state,
}

struct Primitive_request = {
  kind : Primitive_request_kind,
  ordinal : int,
  operand : int,
  role : Request_role,
  domain : Request_domain,
  segment : int,
  segment_image : bits(64),
  width : int,
  range_start : bits(64),
  range_end : bits(64),
  range_length : int,
  range_wrap : bool,
  range_end_at_modulus : bool,
  effective_address : bits(64),
  linear_address : bits(64),
  access : Transaction_access,
  value : bits(64),
  address_translation : bool,
  /* A debug-validation request returns the ordinary response kind after all
     pre-access checks, without performing a memory or device access, and
     places the translated first validation range address (or the point address
     for a non-data request) in response.value.  The reissued
     debug-validated request performs the access at physical_address without
     repeating translation or permission checks. */
  debug_validation : bool,
  debug_validated : bool,
  physical_address : bits(64),
  memory_ranges : list(Memory_access_range),
  /* Pre-access validation can cover bytes that a state record does not transfer.
     Predicated vector requests include only active lanes in both lists. */
  validation_ranges : list(Memory_validation_range),
  read_completion : Read_completion,
  memory_cache_hint : Memory_cache_hint,
  commit_point : bool,
  memory_order : int,
  cache_policy : int,
  suppress_fault : bool,
  staged_updates : list(Staged_register_update),
  selector : bits(64),
  expected : bits(64),
  desired : bits(64),
  auxiliary : bits(64),
  body_length : int,
  payload_bytes : list(bits(8)),
}

struct Commit_destination = {
  kind : Commit_destination_kind,
  index : bits(4),
  width : int,
}

struct Execution_fault = {
  kind : Fault_kind,
  operation : Semantic_operation,
  detail : string,
  error_code : bits(64),
  bus_error : bool,
}

enum Integer_alu_uop_operation = {
    IntegerAluSubtract,
    IntegerAluAnd,
    IntegerAluOr,
    IntegerAluXor,
}

enum Integer_unary_uop_operation = {
    IntegerUnaryNegate,
    IntegerUnaryComplement,
    IntegerUnaryIncrement,
    IntegerUnaryDecrement,
    IntegerUnaryIncrementFlags,
    IntegerUnaryDecrementFlags,
    IntegerUnaryAbsolute,
}

enum Integer_flags_uop_operation = {
    IntegerFlagsCompare,
    IntegerFlagsTest,
}

enum Integer_carry_uop_operation = {
    IntegerCarryAdd,
    IntegerCarrySubtract,
}

enum Integer_shift_uop_operation = {
    IntegerShiftLeft,
    IntegerShiftRight,
    IntegerShiftArithmeticRight,
    IntegerRotateLeft,
    IntegerRotateRight,
}

enum Integer_bit_uop_operation = {
    IntegerBitSet,
    IntegerBitClear,
    IntegerBitChange,
    IntegerBitTest,
}

enum Integer_count_uop_operation = {
    IntegerCountLeadingZero,
    IntegerCountLeadingOne,
    IntegerCountTrailingZero,
    IntegerCountTrailingOne,
    IntegerCountPopulation,
    IntegerCountParity,
    IntegerReverseBytes,
    IntegerReverseBits,
}

enum Integer_bitfield_uop_operation = {
    IntegerBitfieldExtractUnsigned,
    IntegerBitfieldExtractSigned,
    IntegerBitfieldInsert,
}

enum Integer_crc_uop_operation = { IntegerCrc32, IntegerCrc32c }

enum Integer_extend_uop_operation = { IntegerExtendSigned, IntegerExtendUnsigned }

enum Integer_math_uop_operation = {
    IntegerMultiplyLow,
    IntegerMultiplyHighUnsigned,
    IntegerMultiplyHighSigned,
    IntegerMultiplyHighSignedUnsigned,
    IntegerCarrylessMultiplyLow,
    IntegerCarrylessMultiplyHigh,
    IntegerMinimumUnsigned,
    IntegerMaximumUnsigned,
    IntegerMinimumSigned,
    IntegerMaximumSigned,
}

enum Integer_divide_uop_operation = {
    IntegerDivideUnsigned,
    IntegerDivideSigned,
    IntegerRemainderUnsigned,
    IntegerRemainderSigned,
}

enum Core_action_uop_operation = {
    CoreActionHalt,
    CoreActionRelax,
    CoreActionYield,
    CoreActionTrace,
    CoreActionSpeculationFence,
    CoreActionIllegal,
}

enum Atomic_uop_operation = {
    AtomicCompareExchange,
    AtomicCompareExchangePair,
    AtomicFetchAdd,
    AtomicFetchAnd,
    AtomicFetchAndNot,
    AtomicFetchExchange,
    AtomicFetchMaximumSigned,
    AtomicFetchMaximumUnsigned,
    AtomicFetchMinimumSigned,
    AtomicFetchMinimumUnsigned,
    AtomicFetchOr,
    AtomicFetchSubtract,
    AtomicFetchXor,
}

struct Bounds_uop_operation = {
  inclusive_low : bool,
  inclusive_high : bool,
  signed_operation : bool,
}

enum Transaction_family = {
  TransactionControl,
  TransactionEventReturn,
  TransactionSystem,
  TransactionRepeat,
}

struct Environment_uop_operation = {
  family : Transaction_family,
  request_kind : Primitive_request_kind,
  effect_kind : Effect_kind,
  commit_kind : Commit_kind,
  suppress_fault : bool,
}

enum Control_transfer_uop_operation = {
  ControlJump,
  ControlCall,
  ControlFarJump,
  ControlFarCall,
  ControlReturn,
  ControlFarReturn,
}

enum Fused_jump_uop_operation = { FusedCompareJump, FusedTestJump }

enum Event_control_uop_operation = { EventControlBreakpoint, EventControlSyscall, EventControlReturn }

enum Cache_uop_operation = {
  CacheFlushData, CacheInvalidateData, CacheInvalidateInstruction,
  CacheWriteBackData, CacheSynchronize, CachePrefetch, CachePrefetchNonTemporal,
}

enum System_request_uop_operation = { SystemCpuid, SystemPerformanceCounter, SystemWriteControl }

enum System_state_uop_operation = {
  SystemReadControl, SystemReadFlags, SystemReadSegment, SystemReadStatus,
  SystemSetFlags, SystemWriteControlLocal, SystemWriteFlags, SystemWriteSegment,
  SystemWriteStatus,
}

enum Time_uop_operation = { TimeRead, TimeArm, TimeCancel }

enum Tlb_context_uop_operation = {
  TlbInvalidateAsid, TlbInvalidatePage, TlbInvalidateAll, TlbPageTableQuery,
  TlbSwitchPageTable, TlbSwitchPageTableAsid, TlbVirtualToPhysical,
}

enum Vector_branch_uop_operation = { VectorBranchAny, VectorBranchAll, VectorBranchNone }

scattered union Uop_kind

enum Uop_location_kind = {
    UopLocationNone,
    UopLocationRegister,
    UopLocationFpRegister,
    UopLocationSp,
    UopLocationSegment,
    UopLocationImmediate,
    UopLocationTemporary,
    UopLocationEaUpdateAmount,
}

struct Uop_location = {
  kind : Uop_location_kind,
  index : int,
  immediate : bits(64),
  update_difference : option(Ea_update_difference),
  negative : bool,
}

struct Uop = {
  kind : Uop_kind,
  source0 : Uop_location,
  source1 : Uop_location,
  source2 : Uop_location,
  destination : Uop_location,
  destination2 : Uop_location,
  auxiliary : Uop_location,
  operand_name : Operand_id,
  width : int,
  commit_kind : Commit_kind,
}

struct Uop_value = {
  value : bits(64),
  secondary_value : bits(64),
  flags : bits(4),
  write_flags : bool,
  generated_fflags : bits(5),
  bounds_passed : bool,
  cache_policy : int,
  access_class : Memory_access_class,
  physical_class : Physical_memory_class,
  atomic_store_happened : bool,
  body_bytes : list(bits(8)),
  known : bool,
  present : bool,
  address : bool,
  segment : int,
  segment_image : bits(64),
  effective_address : bits(64),
  linear_address : bits(64),
  domain : Request_domain,
}

struct Uop_continuation = {
  program : list(Uop),
  cursor : int,
  values : list(Uop_value),
  pending_destination : int,
  captured : bits(64),
}

struct Repeat_continuation = {
  body : Decoded_instruction,
  bytes : list(bits(8)),
}

struct Transaction_continuation = {
  phase : Continuation_phase,
  ordinal : int,
  captured_first : bits(64),
  captured_second : bits(64),
}

struct Event_continuation = {
  transaction : Transaction_continuation,
  event : Event_record,
  attempt : int,
}

union Pending_continuation = {
  PendingNone : unit,
  PendingControl : Transaction_continuation,
  PendingEventReturn : Transaction_continuation,
  PendingSystem : Transaction_continuation,
  PendingRepeat : Transaction_continuation,
  PendingUop : Uop_continuation,
  PendingEvent : Event_continuation,
}

struct Pending_commit = {
  kind : Commit_kind,
  operation : Semantic_operation,
  destination : Commit_destination,
  after : option(Cpu_state),
  instruction : option(Decoded_instruction),
  continuation : Pending_continuation,
  repeat_parent : option(Repeat_continuation),
  boundary_class : Boundary_class,
}

struct Transaction_response = {
  kind : Transaction_response_kind,
  success : bool,
  fault_kind : Fault_kind,
  fault_cause : int,
  /* A failed memory response identifies the range that supplied the failure. */
  fault_range : option(Memory_validation_range),
  detail : string,
  value : bits(64),
  secondary_value : bits(64),
  flags : bits(4),
  write_flags : bool,
  generated_fflags : bits(5),
  bounds_passed : bool,
  cache_policy : int,
  access_class : Memory_access_class,
  physical_class : Physical_memory_class,
  atomic_store_happened : bool,
  body_bytes : list(bits(8)),
  known : bool,
  present : bool,
}

struct Execution_result = {
  state : Cpu_state,
  pending : Pending_commit,
  request : Primitive_request,
  effects : list(Architectural_effect),
  fault : option(Execution_fault),
  debug_stop : option(Debug_stop),
  awaiting_environment : bool,
}

struct Uop_continue = {
  state : Cpu_state,
  values : list(Uop_value),
}

struct Uop_wait = {
  pending_destination : int,
  request : Primitive_request,
  captured : bits(64),
  commit_state : bool,
}

struct Uop_complete = {
  state : Cpu_state,
  commit_kind : Commit_kind,
  effects : list(Architectural_effect),
  advance_pc : bool,
  boundary_class : Boundary_class,
}

union Uop_step = {
    UopContinue : Uop_continue,
    UopWait : Uop_wait,
    UopComplete : Uop_complete,
    UopDelegate : Execution_result,
    UopFault : Execution_result,
}

// Operation-local entries are generated after the core module.  Core
// continuations resume through this declared boundary without depending on a
// generic route dispatcher.
val execute_operation_entry : (Decoded_instruction, Cpu_state) -> Execution_result
val event_from_fault : Execution_result -> Event_record
val start_repeat_transaction : (Decoded_instruction, Cpu_state) -> Execution_result
val start_event_control : (Event_control_uop_operation, Decoded_instruction, Cpu_state) -> Execution_result
val start_system_request : (System_request_uop_operation, Decoded_instruction, Cpu_state) -> Execution_result
val start_tlb_context_transaction : (Tlb_context_uop_operation, Decoded_instruction, Cpu_state) -> Execution_result

struct Boundary_result = {
  state : Cpu_state,
  admitted : option(Event_record),
  execution : option(Execution_result),
}

struct Decode_execute_outcome = {
  decode : Decode_outcome,
  execution : option(Execution_result),
}
