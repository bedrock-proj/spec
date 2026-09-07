union clause Uop_kind = UopCalcEa : unit
union clause Uop_kind = UopSegmentLea : unit

function clause execute_uop(UopCalcEa(), uop, values, instruction, before, speculative) =
    match operand_named(instruction, uop.operand_name) {
        None() => uop_step_fault(
            faulted(before, instruction.form.operation, IllegalInstruction, "uop EA operand is missing"),
        ),
        Some(operand) => match operand.ea {
            None() => uop_step_fault(
                faulted(before, instruction.form.operation, IllegalInstruction, "uop EA operand has no decoded EA"),
            ),
            Some(ea) => {
                let (evaluation, _) = evaluate_decoded_ea(operand, ea, speculative, before.pc, uop.width);
                let address : Uop_value = struct {
                        value = evaluation.effective_address,
                        secondary_value = 0x0000000000000000,
                        flags = 0x0,
                        write_flags = false,
                        generated_fflags = 0b00000,
                        bounds_passed = false,
                        cache_policy = 0,
                        access_class = NormalAccess,
                        physical_class = NormalPhysical,
                        atomic_store_happened = false,
                        body_bytes = [||],
                        known = false,
                        present = false,
                        address = ea.kind == EaKind_memory,
                        segment = evaluation.segment,
                        segment_image = evaluation.segment_image,
                        effective_address = evaluation.effective_address,
                        linear_address = evaluation.linear_address,
                        domain = request_domain_for_operand(evaluation.domain),
                    };
                uop_step_continue(speculative, uop_value_set(values, uop.destination.index, address))
            },
        },
    }

function clause execute_uop(UopSegmentLea(), uop, values, instruction, before, speculative) =
    match uop_value_get(values, uop.source0.index) {
        Some(address) => {
            let point = segment_point(address.segment_image, address.effective_address);
            var updated = speculative;
            if point.in_bounds then {
                updated.flags = flags_value(false, false, false, false);
                match uop_location_write(uop.destination, point.linear, 8,
                                         instruction.form.operation, updated, values) {
                    Some((next_state, next_values)) => uop_step_continue(next_state, next_values),
                    None() => uop_step_fault(faulted(before, instruction.form.operation,
                        InvalidControlState, "SEGLEA destination is not writable")),
                }
            } else {
                updated.flags = flags_value(false, false, false, true);
                uop_step_continue(updated, values)
            }
        },
        None() => uop_step_fault(faulted(before, instruction.form.operation,
                                    InvalidControlState, "SEGLEA address is unavailable")),
    }
