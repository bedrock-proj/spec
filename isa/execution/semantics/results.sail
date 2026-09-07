function typed_effect(
    kind : Effect_kind,
    instruction : Decoded_instruction,
    state : Cpu_state,
    address : bits(64),
    value : bits(64),
    width : int,
    order : int,
    suppress_fault : bool,
) -> (
    Architectural_effect
) =
    struct {
        kind = kind,
        operation = instruction.form.operation,
        form_id = instruction.form.form_id,
        operands = instruction.operands,
        pc = state.pc,
        status = state.status,
        flags = state.flags,
        fstatus = state.fstatus,
        fflags = state.fflags,
        address = address,
        value = value,
        width = width,
        order = order,
        suppress_fault = suppress_fault,
    }

function empty_destination() -> Commit_destination = struct { kind = DestinationNone, index = 0x0, width = 0 }

function empty_primitive_request() -> Primitive_request = struct {
    kind = NoPrimitiveRequest,
    ordinal = 0,
    operand = 255,
    role = RequestRoleNone,
    domain = RequestDomainCurrent,
    segment = 0,
    segment_image = 0x0000000000000000,
    width = 0,
    range_start = 0x0000000000000000,
    range_end = 0x0000000000000000,
    range_length = 0,
    range_wrap = false,
    range_end_at_modulus = false,
    effective_address = 0x0000000000000000,
    linear_address = 0x0000000000000000,
    access = NoTransactionAccess,
    value = 0x0000000000000000,
    address_translation = false,
    debug_validation = false,
    debug_validated = false,
    physical_address = 0x0000000000000000,
    memory_ranges = [||], validation_ranges = [||],
    read_completion = ReadCompletionData,
    memory_cache_hint = MemoryCacheDefault,
    commit_point = false,
    memory_order = 0,
    cache_policy = 0,
    suppress_fault = false,
    staged_updates = [||],
    selector = 0x0000000000000000,
    expected = 0x0000000000000000,
    desired = 0x0000000000000000,
    auxiliary = 0x0000000000000000,
    body_length = 0,
    payload_bytes = [||],
}

function empty_transaction_continuation() -> Transaction_continuation = struct {
    phase = ContinuationNone,
    ordinal = 0,
    captured_first = 0x0000000000000000,
    captured_second = 0x0000000000000000,
}

function pending_transaction(pending : Pending_commit) -> Transaction_continuation =
    match pending.continuation {
        PendingControl(continuation) => continuation,
        PendingEventReturn(continuation) => continuation,
        PendingSystem(continuation) => continuation,
        PendingRepeat(continuation) => continuation,
        PendingEvent(continuation) => continuation.transaction,
        _ => empty_transaction_continuation(),
    }

function pending_for_family(
    family : Transaction_family,
    transaction : Transaction_continuation,
) -> Pending_continuation =
    match family {
        TransactionControl => PendingControl(transaction),
        TransactionEventReturn => PendingEventReturn(transaction),
        TransactionSystem => PendingSystem(transaction),
        TransactionRepeat => PendingRepeat(transaction),
    }

function pending_with_transaction(
    pending : Pending_commit,
    transaction : Transaction_continuation,
) -> option(Pending_continuation) =
    match pending.continuation {
        PendingEvent(continuation) => Some(PendingEvent({ continuation with transaction = transaction })),
        PendingControl(_) => Some(PendingControl(transaction)),
        PendingEventReturn(_) => Some(PendingEventReturn(transaction)),
        PendingSystem(_) => Some(PendingSystem(transaction)),
        PendingRepeat(_) => Some(PendingRepeat(transaction)),
        _ => None(),
    }

function uop_continuation_get(pending : Pending_commit) -> option(Uop_continuation) =
    match pending.continuation {
        PendingUop(continuation) => Some(continuation),
        _ => None(),
    }

function empty_pending(after : option(Cpu_state), operation : Semantic_operation, kind : Commit_kind) -> Pending_commit =
    struct {
        kind = kind,
        operation = operation,
        destination = empty_destination(),
        after = after,
        instruction = None(),
        continuation = PendingNone(()),
        repeat_parent = None(),
        boundary_class = BoundaryOrdinary,
    }

function completed(
    _before : Cpu_state,
    after : Cpu_state,
    kind : Commit_kind,
    effects : list(Architectural_effect),
) -> (
    Execution_result
) =
    struct {
        state = after,
        pending = empty_pending(None(), Op_NOP, kind),
        request = empty_primitive_request(),
        effects = effects,
        fault = None(),
        debug_stop = None(),
        awaiting_environment = false,
    }

function faulted(
    state : Cpu_state,
    operation : Semantic_operation,
    kind : Fault_kind,
    detail : string,
) -> (
    Execution_result
) =
    struct {
        state = state,
        pending = empty_pending(None(), operation, CommitNone),
        request = empty_primitive_request(),
        effects = [||],
        fault =
            Some(
                struct {
                    kind = kind,
                    operation = operation,
                    detail = detail,
                    error_code =
                        if kind == ExtensionUnavailable
                        then 0x0000000000000003
                        else if kind == DivideOverflow
                             then 0x0000000000000001
                             else if kind == InvalidControlState then 0x0000000000000002 else 0x0000000000000000,
                    bus_error = false,
                },
            ),
        debug_stop = None(),
        awaiting_environment = false,
    }

function debug_stopped(
    state : Cpu_state,
    operation : Semantic_operation,
    stop : Debug_stop,
) -> Execution_result = struct {
    state = state,
    pending = empty_pending(None(), operation, CommitNone),
    request = empty_primitive_request(),
    effects = [||],
    fault = None(),
    debug_stop = Some(stop),
    awaiting_environment = false,
}

function pending_after(result : Execution_result) -> Cpu_state =
    match result.pending.after {
        Some(state) => state,
        None() => result.state,
    }

function faulted_with_error_code(
    state : Cpu_state,
    operation : Semantic_operation,
    kind : Fault_kind,
    detail : string,
    error_code : bits(64),
) -> (
    Execution_result
) = {
    let base = faulted(state, operation, kind, detail);
    {
        base with
        fault =
            Some(
                struct {
                    kind = kind,
                    operation = operation,
                    detail = detail,
                    error_code = error_code,
                    bus_error = false,
                },
            ),
    }
}
