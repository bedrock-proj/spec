from abi.c.model.project import (
    CType,
    LocationPolicy,
    RegisterClass,
    ResolvedRegisterClass,
    ResolvedCallingConvention,
    ResolvedValueClass,
    ValueClass,
    resolve_calling_convention,
)
import unittest
from pathlib import Path
from types import SimpleNamespace

from abi.c.model.call_layout import (
    Argument,
    Call,
    ReturnValue,
    ArgumentAssignment,
    RegisterLocation,
    layout_call,
)
from abi.elf.model.project import (
    DebugRegisterRangeError,
    DebugRegisterRangeErrorReason,
    validate_debug_register_ranges,
)
from abi.elf.model.relocation_metasyntax import (
    RelocationMetasyntax,
    RelocationMetasyntaxError,
)
from engine.isa.project import IsaProject
from engine.isa.registers import Register
from engine.reference import QualifiedReference, Reference
from engine.workspace import load_workspace


class AbiProjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).parents[1]
        cls.workspace = load_workspace(cls.repository)
        cls.isa = cls.workspace.require_provider("isa")
        if not isinstance(cls.isa, IsaProject):
            raise TypeError("workspace isa provider must be an IsaProject")

    def test_debug_register_ranges_reject_invalid_boundaries(self) -> None:
        def assignment(first, last, *, status="reserved", registers=()):
            return SimpleNamespace(
                first=first,
                last=last,
                status=status,
                registers=tuple(registers),
                source=Path(f"range-{first}.yaml"),
            )

        with self.assertRaises(DebugRegisterRangeError) as caught:
            validate_debug_register_ranges(
                (assignment(0, None), assignment(1, None))
            )
        self.assertIs(
            caught.exception.reason,
            DebugRegisterRangeErrorReason.UNBOUNDED_NOT_LAST,
        )
        with self.assertRaises(DebugRegisterRangeError) as caught:
            validate_debug_register_ranges(
                (assignment(0, 0, status="assigned"), assignment(1, None))
            )
        self.assertIs(
            caught.exception.reason,
            DebugRegisterRangeErrorReason.ASSIGNMENT_WIDTH,
        )
        with self.assertRaises(DebugRegisterRangeError) as caught:
            validate_debug_register_ranges((assignment(0, 0),))
        self.assertIs(
            caught.exception.reason,
            DebugRegisterRangeErrorReason.MISSING_UNBOUNDED_TAIL,
        )

    def test_relocation_metasyntax_preserves_and_parses_authored_expression(
        self,
    ) -> None:
        expression = RelocationMetasyntax.parse("got(symbol) + addend - place")
        self.assertEqual(expression.code, "got(symbol) + addend - place")
        self.assertEqual(expression.expression.kind, "sub")
        self.assertEqual(
            expression.evaluate({"symbol": 7, "got:7": 100, "addend": 4, "place": 20}),
            84,
        )
        with self.assertRaises(RelocationMetasyntaxError):
            RelocationMetasyntax.parse("symbol + mystery")

    def test_relocation_resolver_requires_an_integer_result(self) -> None:
        expression = RelocationMetasyntax.parse("resolver(symbol)")
        for invalid in (True, None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "must be an integer"):
                    expression.evaluate(
                        {"symbol": 7, "resolver": lambda _, value=invalid: value}
                    )

    def test_fixed_c_abi_type_size_is_a_positive_whole_byte_count(self) -> None:
        for size_bits in (1, 7, 9):
            with self.subTest(size_bits=size_bits):
                with self.assertRaisesRegex(ValueError, "whole number of bytes"):
                    CType(
                        Reference("base", ("types",), "SYNTHETIC"),
                        Path("type.yaml"),
                        Path("SYNTHETIC"),
                        "SYNTHETIC",
                        "unsigned char",
                        "integer",
                        size_bits,
                        1,
                        None,
                    )

    def test_direct_aggregate_threshold_cannot_exceed_result_capacity(self) -> None:
        registers = tuple(
            Register(
                Reference("base", ("registers", "GPR"), f"R{index}"),
                Path("register.yaml"),
                Path("GPR"),
                "base",
                "GPR",
                f"R{index}",
                64,
                index,
                None,
                None,
                None,
            )
            for index in range(2)
        )
        register_class_reference = Reference(
            "base", ("register_classes",), "SYNTHETIC"
        )
        register_class = RegisterClass(
            register_class_reference,
            Path("register_class.yaml"),
            Path("SYNTHETIC"),
            "SYNTHETIC",
            tuple(QualifiedReference("isa", register.reference) for register in registers),
            tuple(QualifiedReference("isa", register.reference) for register in registers),
            "ascending",
            1,
            "permanent",
        )
        resolved_register_class = ResolvedRegisterClass(
            register_class, registers, registers
        )
        value_class = ValueClass(
            Reference("base", ("value_classes",), "SYNTHETIC"),
            Path("value_class.yaml"),
            Path("SYNTHETIC"),
            "SYNTHETIC",
            ("aggregate",),
            LocationPolicy("copy_address", register_class_reference, 1, 1, None),
            LocationPolicy("size_dependent", register_class_reference, 2, 1, 17),
        )

        with self.assertRaisesRegex(ValueError, "exceeds.*capacity of 16 bytes"):
            ResolvedValueClass(
                value_class, resolved_register_class, resolved_register_class
            )

    def test_hidden_result_pointer_uses_object_pointer_argument_class(self) -> None:
        from dataclasses import replace

        rules = resolve_calling_convention(
            self.workspace.require_provider("abi.c"), self.isa.registers
        )
        pointer_class = rules.value_class("pointer").argument_register_class
        wrong_class = next(
            register_class
            for register_class in rules.register_classes.values()
            if register_class is not pointer_class
            and register_class.arguments[0].width == rules.sret_register.width
        )
        wrong_sret = wrong_class.arguments[0]
        definition = replace(
            rules.definition,
            stack=replace(
                rules.definition.stack,
                sret_register=QualifiedReference("isa", wrong_sret.reference),
            ),
        )

        with self.assertRaisesRegex(ValueError, "object-pointer argument class"):
            ResolvedCallingConvention(
                definition,
                rules.stack_pointer,
                wrong_sret,
                rules.register_classes,
                rules.value_classes,
                rules.promotions,
            )

    def test_c_call_layout_applies_declared_exhaustion_and_result_policies(
        self,
    ) -> None:
        rules = resolve_calling_convention(self.workspace.require_provider("abi.c"), self.isa.registers)
        vector_class = rules.value_class("vector").argument_register_class
        predicate_class = rules.value_class("predicate").argument_register_class
        self.assertIsNotNone(vector_class)
        self.assertIsNotNone(predicate_class)
        assert vector_class is not None
        assert predicate_class is not None
        vector_arguments = tuple(
            Argument(f"v{index}", "vector")
            for index in range(len(vector_class.arguments) + 1)
        )
        predicate_arguments = tuple(
            Argument(f"p{index}", "predicate")
            for index in range(len(predicate_class.arguments) + 1)
        )
        argument_layout = layout_call(
            Call(vector_arguments + predicate_arguments, ReturnValue("void")),
            rules,
        )
        projected = argument_layout.arguments
        vector_overflow = len(vector_class.arguments)
        predicate_start = len(vector_arguments)
        predicate_overflow = predicate_start + len(predicate_class.arguments)
        general = next(
            item for item in rules.register_classes.values()
            if rules.sret_register in item.arguments
        )
        self.assertEqual(
            tuple(item.location for item in projected[:vector_overflow]),
            tuple(RegisterLocation((register,)) for register in vector_class.arguments),
        )
        self.assertEqual(
            projected[vector_overflow],
            ArgumentAssignment(vector_arguments[vector_overflow], "vector", "copy_address", RegisterLocation((general.arguments[0],))),
        )
        self.assertEqual(
            tuple(
                item.location
                for item in projected[predicate_start:predicate_overflow]
            ),
            tuple(RegisterLocation((register,)) for register in predicate_class.arguments),
        )
        self.assertEqual(
            projected[predicate_overflow],
            ArgumentAssignment(predicate_arguments[-1], "predicate", "copy_address", RegisterLocation((general.arguments[1],))),
        )

        aggregate = rules.value_class("aggregate")
        maximum = aggregate.definition.result.direct_maximum_bytes
        self.assertIsNotNone(maximum)
        assert maximum is not None
        direct_result = layout_call(Call((), ReturnValue("aggregate", maximum)), rules)
        indirect_result = layout_call(
            Call((), ReturnValue("aggregate", maximum + 1)), rules
        )
        result_class = aggregate.result_register_class
        self.assertIsNotNone(result_class)
        assert result_class is not None
        result_units = aggregate.definition.result.units
        self.assertIsNotNone(result_units)
        assert result_units is not None
        self.assertEqual(
            direct_result.return_location,
            RegisterLocation(result_class.results[:result_units]),
        )
        self.assertIsNone(direct_result.sret)
        self.assertEqual(
            indirect_result.return_location,
            RegisterLocation((rules.sret_register,)),
        )
        self.assertIs(
            indirect_result.sret,
            rules.sret_register,
        )


if __name__ == "__main__":
    unittest.main()
