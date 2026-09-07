//! Host implementation of the numeric part of Sail floating-point requests.

use core::cmp::Ordering;

use rustc_apfloat::{
    Float, FloatConvert, Round, Status, StatusAnd,
    ieee::{Double, Half, Quad, Single},
};

use crate::{SailCoreNumericOperand, SailCoreNumericRequest, SailCoreNumericResponse};

#[allow(dead_code)]
pub(crate) mod operation {
    include!(concat!(env!("OUT_DIR"), "/semantic_operations.rs"));
}

const RESULT_BITS16: i32 = 9;
const RESULT_BITS32: i32 = 1;
const RESULT_BITS64: i32 = 2;
const RESULT_VALUE_FLAGS: i32 = 5;
const RESULT_INTEGER: i32 = 4;

const VALUE_BITS32: i32 = 1;
const VALUE_BITS64: i32 = 2;
const VALUE_BITS16: i32 = 6;
const VALUE_SIGNED64: i32 = 4;
const VALUE_UNSIGNED64: i32 = 5;

const NAN_NOT: i32 = 0;
const NAN_OPERAND0: i32 = 1;
const NAN_GENERATED_DEFAULT: i32 = 4;

const CAUSE_NV: u8 = 0b1_0000;
const CAUSE_DZ: u8 = 0b0_1000;
const CAUSE_OF: u8 = 0b0_0100;
const CAUSE_UF: u8 = 0b0_0010;
const CAUSE_NX: u8 = 0b0_0001;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UnsupportedNumericOperation(pub i32);

/// Statically linked numeric primitive used by generated Sail code.
///
/// Sail owns instruction control flow and architectural state. This symbol only
/// evaluates one scalar numeric operation and exposes one field of its pure
/// result, so generated C never suspends an instruction for host-side FP work.
#[unsafe(no_mangle)]
pub extern "C" fn bedrock_numeric_primitive(
    operation: i32,
    width: u64,
    result_kind: u64,
    control: u64,
    operand_count: u64,
    kind0: u64,
    bits0: u64,
    kind1: u64,
    bits1: u64,
    kind2: u64,
    bits2: u64,
    transcendental: u64,
    selector: u64,
) -> u64 {
    let count = operand_count.min(3) as usize;
    let kinds = [kind0 as i32, kind1 as i32, kind2 as i32];
    let bits = [bits0, bits1, bits2];
    let mut request = SailCoreNumericRequest {
        valid: true,
        shape: 1,
        element_width: width as i64,
        lane_count: 1,
        result_kind: result_kind as i32,
        operand_count: count as i64,
        rounding_mode: control & 0b11,
        ftz: control & 0b100 != 0,
        daz: control & 0b1000 != 0,
        dn: control & 0b1_0000 != 0,
        transcendental: transcendental != 0,
        ..SailCoreNumericRequest::default()
    };
    for index in 0..count {
        request.operands[index] = SailCoreNumericOperand {
            valid: true,
            kind: kinds[index],
            bits: bits[index],
        };
    }
    let Ok(response) = execute(operation, &request) else {
        return 0;
    };
    match selector {
        0 => response.primary,
        1 => response.secondary,
        2 => {
            (1_u64 << 63)
                | u64::from(response.generated_causes)
                | (u64::from(response.flags_value) << 5)
                | ((response.primary_nan_origin as u64 & 7) << 9)
                | ((response.secondary_nan_origin as u64 & 7) << 12)
                | (u64::from(response.accuracy_mask & 3) << 15)
                | (u64::from(response.error0_q8_8_up) << 17)
                | (u64::from(response.error1_q8_8_up) << 33)
        }
        _ => 0,
    }
}

#[derive(Clone, Copy)]
enum Binary {
    Add,
    Subtract,
    Multiply,
    Divide,
    Modulo,
    Remainder,
}

#[derive(Clone, Copy)]
enum Fused {
    MultiplyAdd,
    MultiplySubtract,
    NegatedMultiplyAdd,
    NegatedMultiplySubtract,
}

pub fn execute(
    operation: i32,
    request: &SailCoreNumericRequest,
) -> Result<SailCoreNumericResponse, UnsupportedNumericOperation> {
    let round = rounding(request.rounding_mode);
    let (prepared, conversion_causes) = prepare_operation_request(operation, request, round);
    let request = &prepared;
    let mut response = SailCoreNumericResponse {
        valid: true,
        flags_mask: request.flags_mask as u8,
        ..SailCoreNumericResponse::default()
    };
    let operands = &request.operands;
    if let Some(base_conversion) = vector_conversion(operation) {
        let conversion = VectorConversion {
            source_fp: matches!(operands[0].kind, VALUE_BITS16 | VALUE_BITS32 | VALUE_BITS64),
            ..base_conversion
        };
        let mut source = operands[0].bits;
        if conversion.source_fp && request.daz && is_subnormal(source, request.element_width) {
            source &= sign_mask(request.element_width);
        }
        let (mut value, causes) =
            convert_vector_value(source, request.element_width as usize, conversion, round);
        response.primary = value;
        response.generated_causes = causes;
        if conversion.destination_fp {
            let width = conversion.destination_width as i64;
            if request.dn && is_nan(value, width) {
                value = default_nan(width);
            }
            if request.ftz && is_subnormal(value, width) {
                value &= sign_mask(width);
                response.generated_causes |= CAUSE_UF | CAUSE_NX;
            }
            response.primary = value;
        }
    } else if exact_after_format_conversion(operation, request, &mut response) {
    } else if operation == operation::OP_FADD {
        binary(request, &mut response, Binary::Add, round);
    } else if operation == operation::OP_FSUB {
        binary(request, &mut response, Binary::Subtract, round);
    } else if operation == operation::OP_FMUL {
        binary(request, &mut response, Binary::Multiply, round);
    } else if operation == operation::OP_FDIV {
        binary(request, &mut response, Binary::Divide, round);
    } else if operation == operation::OP_FMOD {
        binary(request, &mut response, Binary::Modulo, round);
    } else if operation == operation::OP_FREM {
        binary(request, &mut response, Binary::Remainder, round);
    } else if operation == operation::OP_FMADD {
        fused(request, &mut response, Fused::MultiplyAdd, round);
    } else if operation == operation::OP_FMSUB {
        fused(request, &mut response, Fused::MultiplySubtract, round);
    } else if operation == operation::OP_FNMADD {
        fused(request, &mut response, Fused::NegatedMultiplyAdd, round);
    } else if operation == operation::OP_FNMSUB {
        fused(
            request,
            &mut response,
            Fused::NegatedMultiplySubtract,
            round,
        );
    } else if operation == operation::OP_FMIN || operation == operation::OP_FMAX {
        extremum(request, &mut response, operation == operation::OP_FMIN);
    } else if operation == operation::OP_FCMP || operation == operation::OP_FTEST {
        let lhs = if operation == operation::OP_FTEST {
            operands[0].bits
        } else {
            operands[1].bits
        };
        let rhs = if operation == operation::OP_FTEST {
            0
        } else {
            operands[0].bits
        };
        response.flags_value = relation_flags(request, lhs, rhs);
        response.generated_causes = signaling_nan_cause(request);
        debug_assert_eq!(request.result_kind, RESULT_VALUE_FLAGS);
    } else if matches!(
        operation,
        operation::OP_FBNDII | operation::OP_FBNDIX | operation::OP_FBNDXI | operation::OP_FBNDXX
    ) {
        bounds(operation, request, &mut response);
    } else if let Some(conversion) = scalar_conversion(operation) {
        convert_scalar(conversion, request, &mut response, round);
    } else if matches!(
        operation,
        operation::OP_FINT
            | operation::OP_FINTRZ
            | operation::OP_FROUND
            | operation::OP_FTRUNC
            | operation::OP_FCEIL
            | operation::OP_FFLOOR
    ) {
        round_integral(request, &mut response, integral_round(operation, round));
    } else if operation == operation::OP_FGETEXP {
        get_exponent(request, &mut response, round);
    } else if operation == operation::OP_FGETMAN {
        get_mantissa(request, &mut response);
    } else if operation == operation::OP_FSQRT {
        square_root(request, &mut response, round);
    } else if operation == operation::OP_FSCALE {
        scale(request, &mut response, round);
    } else if is_transcendental(operation) {
        transcendental(operation, request, &mut response);
    } else {
        return Err(UnsupportedNumericOperation(operation));
    }
    response.generated_causes |= conversion_causes;
    Ok(response)
}

fn prepare_operation_request(
    operation: i32,
    request: &SailCoreNumericRequest,
    round: Round,
) -> (SailCoreNumericRequest, u8) {
    let mut prepared = *request;
    if scalar_conversion(operation).is_some() || vector_conversion(operation).is_some() {
        return (prepared, 0);
    }
    let destination_width = request.element_width as usize;
    let mut causes = 0;
    for operand in prepared
        .operands
        .iter_mut()
        .take(request.operand_count as usize)
    {
        if !operand.valid || !is_fp_kind(operand.kind) {
            continue;
        }
        let source_width = operand_width(operand.kind, request.element_width) as usize;
        if source_width == destination_width {
            continue;
        }
        let (bits, operand_causes) = if is_nan(operand.bits, source_width as i64) {
            (
                convert_nan(
                    operand.bits,
                    source_width as i64,
                    destination_width as i64,
                ),
                if is_signaling_nan(operand.bits, source_width as i64) {
                    CAUSE_NV
                } else {
                    0
                },
            )
        } else {
            convert_float_width(operand.bits, source_width, destination_width, round)
        };
        operand.kind = value_kind_for_width(destination_width);
        operand.bits = bits;
        causes |= operand_causes;
    }
    (prepared, causes)
}

fn value_kind_for_width(width: usize) -> i32 {
    match width {
        2 => VALUE_BITS16,
        4 => VALUE_BITS32,
        8 => VALUE_BITS64,
        _ => panic!("invalid Sail FP operation width {width}"),
    }
}

fn exact_after_format_conversion(
    operation: i32,
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
) -> bool {
    let source = request.operands[0].bits;
    let width = request.element_width;
    response.primary = if operation == operation::OP_FABS {
        canonical(source, width) & !sign_mask(width)
    } else if operation == operation::OP_FNEG {
        canonical(source, width) ^ sign_mask(width)
    } else if operation == operation::OP_FMOV || operation == operation::OP_FMOVCC {
        canonical(source, width)
    } else {
        return false;
    };
    if is_nan(response.primary, width) {
        response.primary_nan_origin = selected_nan(request)
            .map(|(index, _, _)| NAN_OPERAND0 + index as i32)
            .unwrap_or(NAN_GENERATED_DEFAULT);
    }
    true
}

fn is_transcendental(op: i32) -> bool {
    matches!(
        op,
        operation::OP_FACOSA
            | operation::OP_FASINA
            | operation::OP_FATANA
            | operation::OP_FATANHA
            | operation::OP_FCOSA
            | operation::OP_FCOSHA
            | operation::OP_FETOXA
            | operation::OP_FETOXM1A
            | operation::OP_FLOG10A
            | operation::OP_FLOG2A
            | operation::OP_FLOGNA
            | operation::OP_FLOGNP1A
            | operation::OP_FSINA
            | operation::OP_FSINCOSA
            | operation::OP_FSINHA
            | operation::OP_FTANA
            | operation::OP_FTANHA
            | operation::OP_FTENTOXA
            | operation::OP_FTWOTOXA
    )
}

fn transcendental(
    operation: i32,
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
) {
    let width = request.element_width;
    debug_assert!(matches!(width, 4 | 8));
    let mut input = canonical(request.operands[0].bits, width);
    if request.daz && is_subnormal(input, width) {
        input &= sign_mask(width);
    }
    if is_nan(input, width) {
        finish_float(request, response, Status::OK.and(input));
        set_trans_accuracy(response, width);
        return;
    }
    let magnitude = input & !sign_mask(width);
    let one = if width == 4 {
        0x3f80_0000
    } else {
        0x3ff0_0000_0000_0000
    };
    let pi_over_four = if width == 4 {
        0x3f49_0fda
    } else {
        0x3fe9_21fb_5444_2d18
    };
    let negative_one = one | sign_mask(width);
    let finite = !is_infinity(input, width);
    let domain_ok = if matches!(
        operation,
        operation::OP_FSINA | operation::OP_FCOSA | operation::OP_FTANA | operation::OP_FSINCOSA
    ) {
        finite && magnitude <= pi_over_four
    } else if matches!(
        operation,
        operation::OP_FASINA | operation::OP_FACOSA | operation::OP_FATANHA
    ) {
        finite && magnitude <= one
    } else if matches!(
        operation,
        operation::OP_FLOGNA | operation::OP_FLOG2A | operation::OP_FLOG10A
    ) {
        !sign(input, width) || is_zero(input, width)
    } else if operation == operation::OP_FLOGNP1A {
        !sign(input, width) || input <= negative_one
    } else {
        true
    };
    if !domain_ok {
        finish_float(
            request,
            response,
            Status::INVALID_OP.and(default_nan(width)),
        );
        set_trans_accuracy(response, width);
        return;
    }
    let (first, second) = evaluate_trans(operation, width, input);
    let mut causes = 0;
    if matches!(
        operation,
        operation::OP_FLOGNA | operation::OP_FLOG2A | operation::OP_FLOG10A
    ) && is_zero(input, width)
    {
        causes |= CAUSE_DZ;
    }
    if operation == operation::OP_FLOGNP1A && input == negative_one {
        causes |= CAUSE_DZ;
    }
    if operation == operation::OP_FATANHA && magnitude == one {
        causes |= CAUSE_DZ;
    }
    if finite
        && is_infinity(first, width)
        && !matches!(
            operation,
            operation::OP_FLOGNA
                | operation::OP_FLOG2A
                | operation::OP_FLOG10A
                | operation::OP_FLOGNP1A
                | operation::OP_FATANHA
        )
    {
        causes |= CAUSE_OF;
    }
    if finite && !is_zero(input, width) && (is_subnormal(first, width) || is_zero(first, width)) {
        causes |= CAUSE_UF;
    }
    finish_float(request, response, Status::OK.and(first));
    response.generated_causes |= causes;
    if let Some(second) = second {
        let mut secondary_response = SailCoreNumericResponse::default();
        finish_float(request, &mut secondary_response, Status::OK.and(second));
        response.secondary = secondary_response.primary;
        response.secondary_nan_origin = secondary_response.primary_nan_origin;
    }
    set_trans_accuracy(response, width);
}

fn evaluate_trans(operation: i32, width: i64, input: u64) -> (u64, Option<u64>) {
    if width == 4 {
        let x = fpmath::SoftF32::from_bits(input as u32);
        if operation == operation::OP_FSINCOSA {
            let (a, b) = fpmath::sin_cos(x);
            return (a.to_bits() as u64, Some(b.to_bits() as u64));
        }
        let y = if operation == operation::OP_FSINA {
            fpmath::sin(x)
        } else if operation == operation::OP_FCOSA {
            fpmath::cos(x)
        } else if operation == operation::OP_FTANA {
            fpmath::tan(x)
        } else if operation == operation::OP_FASINA {
            fpmath::asin(x)
        } else if operation == operation::OP_FACOSA {
            fpmath::acos(x)
        } else if operation == operation::OP_FATANA {
            fpmath::atan(x)
        } else if operation == operation::OP_FSINHA {
            fpmath::sinh(x)
        } else if operation == operation::OP_FCOSHA {
            fpmath::cosh(x)
        } else if operation == operation::OP_FTANHA {
            fpmath::tanh(x)
        } else if operation == operation::OP_FATANHA {
            fpmath::atanh(x)
        } else if operation == operation::OP_FETOXA {
            fpmath::exp(x)
        } else if operation == operation::OP_FETOXM1A {
            fpmath::exp_m1(x)
        } else if operation == operation::OP_FTWOTOXA {
            fpmath::exp2(x)
        } else if operation == operation::OP_FTENTOXA {
            fpmath::exp10(x)
        } else if operation == operation::OP_FLOGNA {
            fpmath::log(x)
        } else if operation == operation::OP_FLOGNP1A {
            fpmath::log_1p(x)
        } else if operation == operation::OP_FLOG2A {
            fpmath::log2(x)
        } else {
            fpmath::log10(x)
        };
        (y.to_bits() as u64, None)
    } else {
        let x = fpmath::SoftF64::from_bits(input);
        if operation == operation::OP_FSINCOSA {
            let (a, b) = fpmath::sin_cos(x);
            return (a.to_bits(), Some(b.to_bits()));
        }
        let y = if operation == operation::OP_FSINA {
            fpmath::sin(x)
        } else if operation == operation::OP_FCOSA {
            fpmath::cos(x)
        } else if operation == operation::OP_FTANA {
            fpmath::tan(x)
        } else if operation == operation::OP_FASINA {
            fpmath::asin(x)
        } else if operation == operation::OP_FACOSA {
            fpmath::acos(x)
        } else if operation == operation::OP_FATANA {
            fpmath::atan(x)
        } else if operation == operation::OP_FSINHA {
            fpmath::sinh(x)
        } else if operation == operation::OP_FCOSHA {
            fpmath::cosh(x)
        } else if operation == operation::OP_FTANHA {
            fpmath::tanh(x)
        } else if operation == operation::OP_FATANHA {
            fpmath::atanh(x)
        } else if operation == operation::OP_FETOXA {
            fpmath::exp(x)
        } else if operation == operation::OP_FETOXM1A {
            fpmath::exp_m1(x)
        } else if operation == operation::OP_FTWOTOXA {
            fpmath::exp2(x)
        } else if operation == operation::OP_FTENTOXA {
            fpmath::exp10(x)
        } else if operation == operation::OP_FLOGNA {
            fpmath::log(x)
        } else if operation == operation::OP_FLOGNP1A {
            fpmath::log_1p(x)
        } else if operation == operation::OP_FLOG2A {
            fpmath::log2(x)
        } else {
            fpmath::log10(x)
        };
        (y.to_bits(), None)
    }
}

fn set_trans_accuracy(response: &mut SailCoreNumericResponse, width: i64) {
    if !is_nan(response.primary, width) && !is_infinity(response.primary, width) {
        response.accuracy_mask |= 1;
        response.error0_q8_8_up = 0x0100;
    }
    if response.secondary != 0
        && !is_nan(response.secondary, width)
        && !is_infinity(response.secondary, width)
    {
        response.accuracy_mask |= 2;
        response.error1_q8_8_up = 0x0100;
    }
}

#[derive(Clone, Copy)]
struct VectorConversion {
    destination_width: usize,
    source_fp: bool,
    destination_fp: bool,
    unsigned: bool,
}

fn vector_conversion(operation: i32) -> Option<VectorConversion> {
    let (destination_width, source_fp, destination_fp, unsigned) =
        if operation == operation::OP_VFCVTH {
            (2, true, true, false)
        } else if operation == operation::OP_VFCVTS {
            (4, true, true, false)
        } else if operation == operation::OP_VFCVTD {
            (8, true, true, false)
        } else if operation == operation::OP_VFCVTUH {
            (2, false, true, true)
        } else if operation == operation::OP_VFCVTUS {
            (4, false, true, true)
        } else if operation == operation::OP_VFCVTUD {
            (8, false, true, true)
        } else if operation == operation::OP_VFCVTL {
            (4, true, false, false)
        } else if operation == operation::OP_VFCVTUL {
            (4, true, false, true)
        } else if operation == operation::OP_VFCVTQ {
            (8, true, false, false)
        } else if operation == operation::OP_VFCVTUQ {
            (8, true, false, true)
        } else {
            return None;
        };
    Some(VectorConversion {
        destination_width,
        source_fp,
        destination_fp,
        unsigned,
    })
}

fn convert_vector_value(
    source: u64,
    source_width: usize,
    conversion: VectorConversion,
    round: Round,
) -> (u64, u8) {
    if conversion.source_fp && conversion.destination_fp {
        return convert_float_width(source, source_width, conversion.destination_width, round);
    }
    if conversion.destination_fp {
        let value = if conversion.unsigned {
            source as u128
        } else {
            sign_extend(source, source_width) as i128 as u128
        };
        let result = match (conversion.destination_width, conversion.unsigned) {
            (2, true) => Half::from_u128_r(value, round).map(|v| v.to_bits() as u64),
            (4, true) => Single::from_u128_r(value, round).map(|v| v.to_bits() as u64),
            (8, true) => Double::from_u128_r(value, round).map(|v| v.to_bits() as u64),
            (2, false) => Half::from_i128_r(value as i128, round).map(|v| v.to_bits() as u64),
            (4, false) => Single::from_i128_r(value as i128, round).map(|v| v.to_bits() as u64),
            (8, false) => Double::from_i128_r(value as i128, round).map(|v| v.to_bits() as u64),
            _ => unreachable!(),
        };
        return (result.value, status_causes(result.status));
    }
    let mut exact = true;
    let bits = conversion.destination_width * 8;
    let result = match (source_width, conversion.unsigned) {
        (2, false) => Half::from_bits(source as u128)
            .to_i128_r(bits, Round::TowardZero, &mut exact)
            .map(|v| v as u64),
        (4, false) => Single::from_bits(source as u128)
            .to_i128_r(bits, Round::TowardZero, &mut exact)
            .map(|v| v as u64),
        (8, false) => Double::from_bits(source as u128)
            .to_i128_r(bits, Round::TowardZero, &mut exact)
            .map(|v| v as u64),
        (2, true) => Half::from_bits(source as u128)
            .to_u128_r(bits, Round::TowardZero, &mut exact)
            .map(|v| v as u64),
        (4, true) => Single::from_bits(source as u128)
            .to_u128_r(bits, Round::TowardZero, &mut exact)
            .map(|v| v as u64),
        (8, true) => Double::from_bits(source as u128)
            .to_u128_r(bits, Round::TowardZero, &mut exact)
            .map(|v| v as u64),
        _ => unreachable!(),
    };
    (result.value, status_causes(result.status))
}

fn convert_float_width(
    source: u64,
    source_width: usize,
    destination_width: usize,
    round: Round,
) -> (u64, u8) {
    macro_rules! cvt {
        ($f:ty, $t:ty) => {{
            let mut loses = false;
            let r: StatusAnd<$t> = <$f>::from_bits(source as u128).convert_r(round, &mut loses);
            (r.value.to_bits() as u64, status_causes(r.status))
        }};
    }
    match (source_width, destination_width) {
        (2, 2) | (4, 4) | (8, 8) => (canonical(source, source_width as i64), 0),
        (2, 4) => cvt!(Half, Single),
        (2, 8) => cvt!(Half, Double),
        (4, 2) => cvt!(Single, Half),
        (4, 8) => cvt!(Single, Double),
        (8, 2) => cvt!(Double, Half),
        (8, 4) => cvt!(Double, Single),
        _ => unreachable!(),
    }
}

fn sign_extend(value: u64, width: usize) -> i64 {
    let shift = 64 - width * 8;
    ((value << shift) as i64) >> shift
}

fn square_root(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    round: Round,
) {
    let source = request.operands[0].bits;
    let width = request.element_width;
    if is_nan(source, width) {
        finish_float(request, response, Status::OK.and(source));
        return;
    }
    if sign(source, width) && !is_zero(source, width) {
        finish_float(
            request,
            response,
            Status::INVALID_OP.and(default_nan(width)),
        );
        return;
    }
    if is_zero(source, width) || canonical(source, width) == exponent_mask(width) {
        finish_float(request, response, Status::OK.and(canonical(source, width)));
        return;
    }
    let result = if width == 2 {
        let single = convert_exact::<Half, Single>(Half::from_bits(source as u128));
        let nearest_single = fpmath::sqrt(fpmath::SoftF32::from_bits(single.to_bits() as u32));
        let mut loses = false;
        let nearest: StatusAnd<Half> = Single::from_bits(nearest_single.to_bits() as u128)
            .convert_r(Round::NearestTiesToEven, &mut loses);
        directed_sqrt::<Half>(source, nearest.value.to_bits() as u64, round)
    } else if width == 4 {
        let nearest = fpmath::sqrt(fpmath::SoftF32::from_bits(source as u32)).to_bits() as u64;
        directed_sqrt::<Single>(source, nearest, round)
    } else {
        let nearest = fpmath::sqrt(fpmath::SoftF64::from_bits(source)).to_bits();
        directed_sqrt::<Double>(source, nearest, round)
    };
    finish_float(request, response, result);
}

fn directed_sqrt<F>(source: u64, nearest: u64, round: Round) -> StatusAnd<u64>
where
    F: Float + FloatConvert<Quad>,
{
    let source = F::from_bits(source as u128);
    let nearest = F::from_bits(nearest as u128);
    let source_quad = convert_exact::<F, Quad>(source);
    let nearest_quad = convert_exact::<F, Quad>(nearest);
    let square = nearest_quad
        .mul_r(nearest_quad, Round::NearestTiesToEven)
        .value;
    let relation = square
        .partial_cmp(&source_quad)
        .expect("finite square-root operands are ordered");
    let value = match (round, relation) {
        (_, Ordering::Equal) | (Round::NearestTiesToEven, _) => nearest,
        (Round::TowardPositive, Ordering::Less) => nearest.next_up().value,
        (Round::TowardPositive, Ordering::Greater) => nearest,
        (Round::TowardZero | Round::TowardNegative, Ordering::Greater) => nearest.next_down().value,
        (Round::TowardZero | Round::TowardNegative, Ordering::Less) => nearest,
        (Round::NearestTiesToAway, _) => unreachable!("ISA has no ties-away rounding mode"),
    };
    let status = if relation == Ordering::Equal {
        Status::OK
    } else {
        Status::INEXACT
    };
    status.and(value.to_bits() as u64)
}

fn scale(request: &SailCoreNumericRequest, response: &mut SailCoreNumericResponse, round: Round) {
    let exponent = request.operands[0].bits;
    let value = request.operands[1].bits;
    let result = if request.element_width == 4 {
        scale_float::<Single>(exponent, value, round)
    } else {
        scale_float::<Double>(exponent, value, round)
    };
    finish_float(request, response, result);
}

fn scale_float<F>(exponent: u64, value: u64, round: Round) -> StatusAnd<u64>
where
    F: Float + FloatConvert<Quad>,
    Quad: FloatConvert<F>,
{
    let exponent = F::from_bits(exponent as u128).round_to_integral(round);
    let mut exact = false;
    let amount = exponent
        .value
        .to_i128_r(128, Round::TowardZero, &mut exact)
        .value
        .clamp(-4096, 4096) as i32;
    let scaled = convert_exact::<F, Quad>(F::from_bits(value as u128))
        .scalbn_r(amount, Round::NearestTiesToEven);
    let mut loses_info = false;
    let converted: StatusAnd<F> = scaled.convert_r(round, &mut loses_info);
    StatusAnd {
        status: exponent.status | converted.status,
        value: converted.value.to_bits() as u64,
    }
}

fn convert_exact<F, T>(value: F) -> T
where
    F: Float + FloatConvert<T>,
    T: Float,
{
    let mut loses_info = false;
    value
        .convert_r(Round::NearestTiesToEven, &mut loses_info)
        .value
}

fn integral_round(operation: i32, dynamic: Round) -> Round {
    if operation == operation::OP_FINT {
        dynamic
    } else if operation == operation::OP_FROUND {
        Round::NearestTiesToEven
    } else if operation == operation::OP_FCEIL {
        Round::TowardPositive
    } else if operation == operation::OP_FFLOOR {
        Round::TowardNegative
    } else {
        Round::TowardZero
    }
}

fn rounding(raw: u64) -> Round {
    match raw & 3 {
        0 => Round::NearestTiesToEven,
        1 => Round::TowardZero,
        2 => Round::TowardNegative,
        _ => Round::TowardPositive,
    }
}

fn binary(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    operation: Binary,
    round: Round,
) {
    let src = request.operands[0].bits;
    let dst = request.operands[1].bits;
    let computed = match request.element_width {
        2 => calculate_binary::<Half>(src, dst, operation, round),
        4 => calculate_binary::<Single>(src, dst, operation, round),
        8 => calculate_binary::<Double>(src, dst, operation, round),
        width => panic!("invalid Sail FP operation width {width}"),
    };
    finish_float(request, response, computed);
}

fn calculate_binary<F: Float>(
    src: u64,
    dst: u64,
    operation: Binary,
    round: Round,
) -> StatusAnd<u64> {
    let src = F::from_bits(src as u128);
    let dst = F::from_bits(dst as u128);
    let computed = match operation {
        Binary::Add => dst.add_r(src, round),
        Binary::Subtract => dst.sub_r(src, round),
        Binary::Multiply => dst.mul_r(src, round),
        Binary::Divide => dst.div_r(src, round),
        Binary::Modulo => dst.c_fmod(src),
        Binary::Remainder => dst.ieee_rem(src),
    };
    computed.map(|value| value.to_bits() as u64)
}

fn fused(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    operation: Fused,
    round: Round,
) {
    let [lhs, rhs, dst] = request.operands.map(|operand| operand.bits);
    let computed = match request.element_width {
        2 => calculate_fused::<Half>(lhs, rhs, dst, operation, round),
        4 => calculate_fused::<Single>(lhs, rhs, dst, operation, round),
        8 => calculate_fused::<Double>(lhs, rhs, dst, operation, round),
        width => panic!("invalid Sail FP operation width {width}"),
    };
    finish_float(request, response, computed);
}

fn calculate_fused<F: Float>(
    lhs: u64,
    rhs: u64,
    dst: u64,
    operation: Fused,
    round: Round,
) -> StatusAnd<u64> {
    let lhs = F::from_bits(lhs as u128);
    let rhs = F::from_bits(rhs as u128);
    let dst = F::from_bits(dst as u128);
    let computed = match operation {
        Fused::MultiplyAdd => lhs.mul_add_r(rhs, dst, round),
        Fused::MultiplySubtract => lhs.mul_add_r(rhs, -dst, round),
        Fused::NegatedMultiplyAdd => (-lhs).mul_add_r(rhs, -dst, round),
        Fused::NegatedMultiplySubtract => (-lhs).mul_add_r(rhs, dst, round),
    };
    computed.map(|value| value.to_bits() as u64)
}

fn finish_float(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    computed: StatusAnd<u64>,
) {
    finish_float_width(request, response, computed, request.element_width);
}

fn finish_float_width(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    computed: StatusAnd<u64>,
    width: i64,
) {
    let source_nan = selected_nan(request);
    let computed_is_nan = is_nan(computed.value, width);
    response.primary = if computed_is_nan {
        source_nan
            .map(|(_, bits, source_width)| convert_nan(bits, source_width, width))
            .unwrap_or_else(|| default_nan(width))
    } else {
        canonical(computed.value, width)
    };
    response.primary_nan_origin = if computed_is_nan {
        source_nan
            .map(|(index, _, _)| NAN_OPERAND0 + index as i32)
            .unwrap_or(NAN_GENERATED_DEFAULT)
    } else {
        NAN_NOT
    };
    response.generated_causes = status_causes(computed.status) | signaling_nan_cause(request);
}

fn extremum(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    minimum: bool,
) {
    let src = request.operands[0].bits;
    let dst = request.operands[1].bits;
    let width = request.element_width;
    let src_nan = is_nan(src, width);
    let dst_nan = is_nan(dst, width);
    response.primary = match (src_nan, dst_nan) {
        (true, true) => {
            let (_, bits, source_width) = selected_nan(request).unwrap();
            convert_nan(bits, source_width, width)
        }
        (true, false) => canonical(dst, width),
        (false, true) => canonical(src, width),
        (false, false) if is_zero(src, width) && is_zero(dst, width) => {
            let negative = if minimum {
                sign(src, width) || sign(dst, width)
            } else {
                sign(src, width) && sign(dst, width)
            };
            if negative { sign_mask(width) } else { 0 }
        }
        (false, false) => {
            let less = numeric_order(request, src, dst) == Some(Ordering::Less);
            if (minimum && less) || (!minimum && !less) {
                src
            } else {
                dst
            }
        }
    };
    if is_nan(response.primary, width) {
        response.primary_nan_origin = selected_nan(request)
            .map(|(index, _, _)| NAN_OPERAND0 + index as i32)
            .unwrap_or(NAN_GENERATED_DEFAULT);
    }
    response.generated_causes = signaling_nan_cause(request);
}

fn relation_flags(request: &SailCoreNumericRequest, lhs: u64, rhs: u64) -> u8 {
    match numeric_order(request, lhs, rhs) {
        Some(Ordering::Greater) => 0,
        Some(Ordering::Equal) => 0b1000,
        Some(Ordering::Less) => 0b0110,
        None => 0b0001,
    }
}

fn bounds(
    operation: i32,
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
) {
    let [low, value, high] = request.operands.map(|operand| operand.bits);
    let low_relation = numeric_order(request, value, low);
    let high_relation = numeric_order(request, value, high);
    let low_inclusive = matches!(operation, operation::OP_FBNDXI | operation::OP_FBNDXX);
    let high_inclusive = matches!(operation, operation::OP_FBNDIX | operation::OP_FBNDXX);
    let low_ok = low_relation == Some(Ordering::Greater)
        || low_inclusive && low_relation == Some(Ordering::Equal);
    let high_ok = high_relation == Some(Ordering::Less)
        || high_inclusive && high_relation == Some(Ordering::Equal);
    response.flags_value = if low_ok && high_ok { 0 } else { 1 };
    response.generated_causes = signaling_nan_cause(request);
}

#[derive(Clone, Copy)]
struct ScalarConversion {
    fn_width: usize,
    unsigned: bool,
}

fn scalar_conversion(operation: i32) -> Option<ScalarConversion> {
    let fn_width = if matches!(operation, operation::OP_FCVTH | operation::OP_FCVTUH) {
        2
    } else if matches!(operation, operation::OP_FCVTS | operation::OP_FCVTUS) {
        4
    } else if matches!(operation, operation::OP_FCVTD | operation::OP_FCVTUD) {
        8
    } else {
        return None;
    };
    Some(ScalarConversion {
        fn_width,
        unsigned: matches!(
            operation,
            operation::OP_FCVTUH | operation::OP_FCVTUS | operation::OP_FCVTUD
        ),
    })
}

fn result_float_width(result_kind: i32) -> Option<usize> {
    match result_kind {
        RESULT_BITS16 => Some(2),
        RESULT_BITS32 => Some(4),
        RESULT_BITS64 => Some(8),
        _ => None,
    }
}

fn convert_scalar(
    conversion: ScalarConversion,
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    round: Round,
) {
    let source = request.operands[0];
    match (source.kind, request.result_kind) {
        (VALUE_SIGNED64, _) => {
            let value = source.bits as i64 as i128;
            let result = match conversion.fn_width {
                2 => Half::from_i128_r(value, round).map(|value| value.to_bits() as u64),
                4 => Single::from_i128_r(value, round).map(|value| value.to_bits() as u64),
                8 => Double::from_i128_r(value, round).map(|value| value.to_bits() as u64),
                _ => unreachable!(),
            };
            finish_float_width(request, response, result, conversion.fn_width as i64);
        }
        (VALUE_UNSIGNED64, _) => {
            let result = match conversion.fn_width {
                2 => Half::from_u128_r(source.bits as u128, round)
                    .map(|value| value.to_bits() as u64),
                4 => Single::from_u128_r(source.bits as u128, round)
                    .map(|value| value.to_bits() as u64),
                8 => Double::from_u128_r(source.bits as u128, round)
                    .map(|value| value.to_bits() as u64),
                _ => unreachable!(),
            };
            finish_float_width(request, response, result, conversion.fn_width as i64);
        }
        (VALUE_BITS16 | VALUE_BITS32 | VALUE_BITS64, RESULT_INTEGER) => {
            let mut exact = true;
            let integer_bits = request.element_width as usize * 8;
            let result = match (source.kind, conversion.unsigned) {
                (VALUE_BITS16, false) => Half::from_bits(source.bits as u128)
                    .to_i128_r(integer_bits, Round::TowardZero, &mut exact)
                    .map(|value| value as u64),
                (VALUE_BITS32, false) => Single::from_bits(source.bits as u128)
                    .to_i128_r(integer_bits, Round::TowardZero, &mut exact)
                    .map(|value| value as u64),
                (VALUE_BITS64, false) => Double::from_bits(source.bits as u128)
                    .to_i128_r(integer_bits, Round::TowardZero, &mut exact)
                    .map(|value| value as u64),
                (VALUE_BITS16, true) => Half::from_bits(source.bits as u128)
                    .to_u128_r(integer_bits, Round::TowardZero, &mut exact)
                    .map(|value| value as u64),
                (VALUE_BITS32, true) => Single::from_bits(source.bits as u128)
                    .to_u128_r(integer_bits, Round::TowardZero, &mut exact)
                    .map(|value| value as u64),
                (VALUE_BITS64, true) => Double::from_bits(source.bits as u128)
                    .to_u128_r(integer_bits, Round::TowardZero, &mut exact)
                    .map(|value| value as u64),
                _ => unreachable!(),
            };
            response.primary = result.value;
            response.generated_causes = if result.status.contains(Status::INVALID_OP) {
                CAUSE_NV
            } else {
                status_causes(result.status)
            };
        }
        (VALUE_BITS16 | VALUE_BITS32 | VALUE_BITS64, _) => {
            let source_width = operand_width(source.kind, request.element_width) as usize;
            let destination_width = result_float_width(request.result_kind)
                .expect("floating conversion must have a floating result kind");
            let (value, causes) =
                convert_float_width(source.bits, source_width, destination_width, round);
            finish_float_width(
                request,
                response,
                Status::OK.and(value),
                destination_width as i64,
            );
            response.generated_causes |= causes;
        }
        _ => unreachable!("invalid Sail FP conversion request {request:?}"),
    }
}

fn round_integral(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    round: Round,
) {
    let source = request.operands[0].bits;
    let result = if request.element_width == 2 {
        Half::from_bits(source as u128)
            .round_to_integral(round)
            .map(|value| value.to_bits() as u64)
    } else if request.element_width == 4 {
        Single::from_bits(source as u128)
            .round_to_integral(round)
            .map(|value| value.to_bits() as u64)
    } else {
        Double::from_bits(source as u128)
            .round_to_integral(round)
            .map(|value| value.to_bits() as u64)
    };
    finish_float(request, response, result);
}

fn get_exponent(
    request: &SailCoreNumericRequest,
    response: &mut SailCoreNumericResponse,
    round: Round,
) {
    let source = canonical(request.operands[0].bits, request.element_width);
    let (field, bias) = if request.element_width == 4 {
        ((source & exponent_mask(4)) >> 23, 127_i128)
    } else {
        ((source & exponent_mask(8)) >> 52, 1023_i128)
    };
    let exponent = if field == 0 { 1 } else { field as i128 } - bias;
    let result = if request.element_width == 4 {
        Single::from_i128_r(exponent, round).map(|value| value.to_bits() as u64)
    } else {
        Double::from_i128_r(exponent, round).map(|value| value.to_bits() as u64)
    };
    finish_float(request, response, result);
}

fn get_mantissa(request: &SailCoreNumericRequest, response: &mut SailCoreNumericResponse) {
    let source = canonical(request.operands[0].bits, request.element_width);
    if is_nan(source, request.element_width) {
        finish_float(request, response, Status::OK.and(source));
        return;
    }
    let exponent = source & exponent_mask(request.element_width);
    let normal = exponent != 0 && exponent != exponent_mask(request.element_width);
    let value = if normal {
        source & !exponent_mask(request.element_width)
            | if request.element_width == 4 {
                0x3f80_0000
            } else {
                0x3ff0_0000_0000_0000
            }
    } else {
        source
    };
    finish_float(request, response, Status::OK.and(value));
}

fn numeric_order(request: &SailCoreNumericRequest, lhs: u64, rhs: u64) -> Option<Ordering> {
    match request.element_width {
        2 => Half::from_bits(lhs as u128).partial_cmp(&Half::from_bits(rhs as u128)),
        4 => Single::from_bits(lhs as u128).partial_cmp(&Single::from_bits(rhs as u128)),
        8 => Double::from_bits(lhs as u128).partial_cmp(&Double::from_bits(rhs as u128)),
        width => panic!("invalid Sail FP operation width {width}"),
    }
}

fn selected_nan(request: &SailCoreNumericRequest) -> Option<(usize, u64, i64)> {
    for signaling in [true, false] {
        for (index, operand) in request.operands.iter().enumerate() {
            if index >= request.operand_count as usize
                || !operand.valid
                || !is_fp_kind(operand.kind)
            {
                continue;
            }
            let width = operand_width(operand.kind, request.element_width);
            if is_nan(operand.bits, width) && is_signaling_nan(operand.bits, width) == signaling {
                return Some((index, operand.bits, width));
            }
        }
    }
    None
}

fn signaling_nan_cause(request: &SailCoreNumericRequest) -> u8 {
    request.operands[..request.operand_count as usize]
        .iter()
        .any(|operand| {
            is_fp_kind(operand.kind)
                && is_signaling_nan(
                    operand.bits,
                    operand_width(operand.kind, request.element_width),
                )
        })
        .then_some(CAUSE_NV)
        .unwrap_or(0)
}

fn is_fp_kind(kind: i32) -> bool {
    matches!(kind, VALUE_BITS16 | VALUE_BITS32 | VALUE_BITS64)
}

fn operand_width(kind: i32, fallback: i64) -> i64 {
    match kind {
        VALUE_BITS16 => 2,
        VALUE_BITS32 => 4,
        VALUE_BITS64 => 8,
        _ => fallback,
    }
}

fn status_causes(status: Status) -> u8 {
    (status.contains(Status::INVALID_OP) as u8) * CAUSE_NV
        | (status.contains(Status::DIV_BY_ZERO) as u8) * CAUSE_DZ
        | (status.contains(Status::OVERFLOW) as u8) * CAUSE_OF
        | (status.contains(Status::UNDERFLOW) as u8) * CAUSE_UF
        | (status.contains(Status::INEXACT) as u8) * CAUSE_NX
}

fn canonical(bits: u64, width: i64) -> u64 {
    if width == 2 {
        bits & u16::MAX as u64
    } else if width == 4 {
        bits & u32::MAX as u64
    } else {
        bits
    }
}

fn sign_mask(width: i64) -> u64 {
    if width == 2 {
        1 << 15
    } else if width == 4 {
        1 << 31
    } else {
        1 << 63
    }
}

fn exponent_mask(width: i64) -> u64 {
    if width == 2 {
        0x7c00
    } else if width == 4 {
        0x7f80_0000
    } else {
        0x7ff0_0000_0000_0000
    }
}

fn fraction_mask(width: i64) -> u64 {
    if width == 2 {
        0x03ff
    } else if width == 4 {
        0x007f_ffff
    } else {
        0x000f_ffff_ffff_ffff
    }
}

fn quiet_bit(width: i64) -> u64 {
    if width == 2 {
        0x0200
    } else if width == 4 {
        0x0040_0000
    } else {
        0x0008_0000_0000_0000
    }
}

fn is_zero(bits: u64, width: i64) -> bool {
    canonical(bits, width) & !sign_mask(width) == 0
}

fn sign(bits: u64, width: i64) -> bool {
    canonical(bits, width) & sign_mask(width) != 0
}

fn is_nan(bits: u64, width: i64) -> bool {
    let bits = canonical(bits, width);
    bits & exponent_mask(width) == exponent_mask(width) && bits & fraction_mask(width) != 0
}

fn is_infinity(bits: u64, width: i64) -> bool {
    let bits = canonical(bits, width);
    bits & exponent_mask(width) == exponent_mask(width) && bits & fraction_mask(width) == 0
}

fn is_subnormal(bits: u64, width: i64) -> bool {
    let bits = canonical(bits, width);
    bits & exponent_mask(width) == 0 && bits & fraction_mask(width) != 0
}

fn is_signaling_nan(bits: u64, width: i64) -> bool {
    is_nan(bits, width) && bits & quiet_bit(width) == 0
}

fn quiet_nan(bits: u64, width: i64) -> u64 {
    canonical(bits, width) | quiet_bit(width)
}

fn convert_nan(bits: u64, source_width: i64, result_width: i64) -> u64 {
    let quiet = quiet_nan(bits, source_width);
    match (source_width, result_width) {
        (2, 2) | (4, 4) | (8, 8) => quiet,
        (2, 4) => (quiet & 0x8000) << 16 | 0x7f80_0000 | (quiet & 0x03ff) << 13,
        (2, 8) => (quiet & 0x8000) << 48 | 0x7ff0_0000_0000_0000 | (quiet & 0x03ff) << 42,
        (4, 2) => (quiet >> 16) & 0x8000 | 0x7c00 | (quiet >> 13) & 0x03ff,
        (8, 2) => (quiet >> 48) & 0x8000 | 0x7c00 | (quiet >> 42) & 0x03ff,
        (4, 8) => (quiet & (1 << 31)) << 32 | 0x7ff0_0000_0000_0000 | (quiet & 0x007f_ffff) << 29,
        (8, 4) => (quiet >> 32) & (1 << 31) | 0x7f80_0000 | (quiet >> 29) & 0x007f_ffff,
        _ => default_nan(result_width),
    }
}

fn default_nan(width: i64) -> u64 {
    if width == 2 {
        0x7e00
    } else if width == 4 {
        0x7fc0_0000
    } else {
        0x7ff8_0000_0000_0000
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::SailCoreNumericOperand;

    fn binary_request(width: i64, src: u64, dst: u64) -> SailCoreNumericRequest {
        SailCoreNumericRequest {
            valid: true,
            element_width: width,
            result_kind: if width == 4 {
                RESULT_BITS32
            } else {
                RESULT_BITS64
            },
            operand_count: 2,
            operands: [
                SailCoreNumericOperand {
                    valid: true,
                    kind: if width == 4 { 1 } else { 2 },
                    bits: src,
                },
                SailCoreNumericOperand {
                    valid: true,
                    kind: if width == 4 { 1 } else { 2 },
                    bits: dst,
                },
                SailCoreNumericOperand::default(),
            ],
            allowed_causes: 0b1_0111,
            ..SailCoreNumericRequest::default()
        }
    }

    #[test]
    fn add_uses_architectural_source_destination_order() {
        let response = execute(
            operation::OP_FADD,
            &binary_request(8, 2.0_f64.to_bits(), 3.0_f64.to_bits()),
        )
        .unwrap();
        assert_eq!(response.primary, 5.0_f64.to_bits());
        assert_eq!(response.generated_causes, 0);
    }

    #[test]
    fn signaling_nan_is_quietened_with_source_origin_and_invalid_cause() {
        let response = execute(
            operation::OP_FADD,
            &binary_request(4, 0x7f80_0123, 0x3f80_0000),
        )
        .unwrap();
        assert_eq!(response.primary, 0x7fc0_0123);
        assert_eq!(response.primary_nan_origin, NAN_OPERAND0);
        assert_eq!(response.generated_causes, CAUSE_NV);
    }

    #[test]
    fn square_root_uses_deterministic_soft_float_result() {
        let request = SailCoreNumericRequest {
            valid: true,
            element_width: 8,
            result_kind: RESULT_BITS64,
            operand_count: 1,
            operands: [
                SailCoreNumericOperand {
                    valid: true,
                    kind: VALUE_BITS64,
                    bits: 4.0_f64.to_bits(),
                },
                SailCoreNumericOperand::default(),
                SailCoreNumericOperand::default(),
            ],
            ..SailCoreNumericRequest::default()
        };
        let response = execute(operation::OP_FSQRT, &request).unwrap();
        assert_eq!(response.primary, 2.0_f64.to_bits());
        assert_eq!(response.generated_causes, 0);
    }

    #[test]
    fn format_conversion_preserves_nan_payload_and_origin() {
        let request = SailCoreNumericRequest {
            valid: true,
            element_width: 8,
            result_kind: RESULT_BITS64,
            operand_count: 1,
            operands: [
                SailCoreNumericOperand {
                    valid: true,
                    kind: VALUE_BITS32,
                    bits: 0xffc0_0123,
                },
                SailCoreNumericOperand::default(),
                SailCoreNumericOperand::default(),
            ],
            ..SailCoreNumericRequest::default()
        };
        let response = execute(operation::OP_FCVTD, &request).unwrap();
        assert_eq!(response.primary, 0xfff8_0024_6000_0000);
        assert_eq!(response.primary_nan_origin, NAN_OPERAND0);
    }

    #[test]
    fn immediate_source_format_is_converted_before_binary_operation() {
        let mut request = binary_request(8, 1.0_f32.to_bits() as u64, 0.0_f64.to_bits());
        request.operands[0].kind = VALUE_BITS32;
        let response = execute(operation::OP_FADD, &request).unwrap();
        assert_eq!(response.primary, 1.0_f64.to_bits());
        assert_eq!(response.generated_causes, 0);
    }

    #[test]
    fn immediate_source_conversion_causes_are_combined_with_operation_causes() {
        let mut request = binary_request(4, f64::MAX.to_bits(), 0.0_f32.to_bits() as u64);
        request.operands[0].kind = VALUE_BITS64;
        let response = execute(operation::OP_FADD, &request).unwrap();
        assert_eq!(response.primary, f32::INFINITY.to_bits() as u64);
        assert_eq!(response.generated_causes, CAUSE_OF | CAUSE_NX);
    }

    #[test]
    fn immediate_source_format_is_converted_for_flags_result() {
        let request = SailCoreNumericRequest {
            valid: true,
            element_width: 8,
            result_kind: RESULT_VALUE_FLAGS,
            operand_count: 2,
            operands: [
                SailCoreNumericOperand {
                    valid: true,
                    kind: VALUE_BITS32,
                    bits: 1.0_f32.to_bits() as u64,
                },
                SailCoreNumericOperand {
                    valid: true,
                    kind: VALUE_BITS64,
                    bits: 1.0_f64.to_bits(),
                },
                SailCoreNumericOperand::default(),
            ],
            ..SailCoreNumericRequest::default()
        };
        let response = execute(operation::OP_FCMP, &request).unwrap();
        assert_eq!(response.flags_value, 0b1000);
        assert_eq!(response.generated_causes, 0);
    }

    #[test]
    fn exact_operation_runs_after_immediate_source_conversion() {
        let request = SailCoreNumericRequest {
            valid: true,
            element_width: 8,
            result_kind: RESULT_BITS64,
            operand_count: 1,
            operands: [
                SailCoreNumericOperand {
                    valid: true,
                    kind: VALUE_BITS32,
                    bits: (-1.0_f32).to_bits() as u64,
                },
                SailCoreNumericOperand::default(),
                SailCoreNumericOperand::default(),
            ],
            ..SailCoreNumericRequest::default()
        };
        let response = execute(operation::OP_FABS, &request).unwrap();
        assert_eq!(response.primary, 1.0_f64.to_bits());
        assert_eq!(response.generated_causes, 0);
    }
}
