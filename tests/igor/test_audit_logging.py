"""
test_audit_logging.py — T-detailed-logging-audit

Tests for the static AST classifier and report aggregation. Runtime pass
exercised separately against real logs.
"""

import ast
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.claudecode.audit_logging import (  # noqa: E402
    PATTERN_GET_LOGGER,
    PATTERN_LEGACY_LOG,
    PATTERN_LOG_ERROR,
    PATTERN_LOGGING_GETLOGGER,
    PATTERN_PRINT,
    PATTERN_SELF_LOG,
    SEV_BYPASS,
    SEV_GOOD,
    SEV_LEGACY,
    SEV_SMELL,
    _CallsiteWalker,
    _classify_call,
    _collect_classes,
    aggregate,
    audit_file,
    group_for_migration,
)


def _walk(source: str, in_test: bool = False) -> list:
    from lab.claudecode.audit_logging import _scan_logger_assignments

    tree = ast.parse(source)
    assignments = _scan_logger_assignments(tree)
    walker = _CallsiteWalker(
        source.splitlines(), "<test>", in_test, logger_assignments=assignments
    )
    walker.visit(tree)
    return walker.callsites


class TestClassifyCall:
    def test_print_call(self):
        tree = ast.parse("print('hello')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_PRINT

    def test_self_log_info(self):
        tree = ast.parse("self.log.info('msg')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_SELF_LOG

    def test_self_log_warning(self):
        tree = ast.parse("self.log.warning('msg')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_SELF_LOG

    def test_log_error_function(self):
        tree = ast.parse("log_error(kind='X', detail='y')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_LOG_ERROR

    def test_logging_getlogger_direct(self):
        tree = ast.parse("logging.getLogger(__name__)")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_LOGGING_GETLOGGER

    def test_get_logger_helper(self):
        tree = ast.parse("get_logger(__name__)")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_GET_LOGGER

    def test_get_logger_chained(self):
        tree = ast.parse("get_logger(__name__).warning('x')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_GET_LOGGER

    def test_logging_getlogger_chained(self):
        tree = ast.parse("logging.getLogger(__name__).info('x')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_LOGGING_GETLOGGER

    def test_legacy_log_var(self):
        tree = ast.parse("_log.warning('x')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_LEGACY_LOG

    def test_legacy_logger_var(self):
        tree = ast.parse("logger.info('x')")
        call = tree.body[0].value
        assert _classify_call(call) == PATTERN_LEGACY_LOG

    def test_unrelated_call_returns_none(self):
        tree = ast.parse("foo.bar()")
        call = tree.body[0].value
        assert _classify_call(call) is None

    def test_dict_get_not_misclassified(self):
        tree = ast.parse("d.get('x')")
        call = tree.body[0].value
        assert _classify_call(call) is None


class TestScanLoggerAssignments:
    def test_get_logger_assignment_recognized(self):
        from lab.claudecode.audit_logging import _scan_logger_assignments

        tree = ast.parse("_log = get_logger(__name__)\n")
        out = _scan_logger_assignments(tree)
        assert out == {"_log": "get_logger"}

    def test_logging_getlogger_assignment_recognized(self):
        from lab.claudecode.audit_logging import _scan_logger_assignments

        tree = ast.parse("logger = logging.getLogger(__name__)\n")
        out = _scan_logger_assignments(tree)
        assert out == {"logger": "logging.getLogger"}

    def test_log_var_with_get_logger_classified_as_good(self):
        source = textwrap.dedent("""
            _log = get_logger(__name__)
            _log.warning('x')
        """)
        sites = _walk(source)
        # Find the _log.warning callsite (skip the get_logger() call itself)
        warn_sites = [
            s for s in sites if s.pattern in (PATTERN_GET_LOGGER, PATTERN_LEGACY_LOG)
        ]
        # _log.warning(...) — assignment from get_logger → GET_LOGGER good
        warning_call = next(s for s in warn_sites if "warning" in s.snippet)
        assert warning_call.pattern == PATTERN_GET_LOGGER
        assert warning_call.severity == SEV_GOOD

    def test_log_var_with_logging_getlogger_classified_as_legacy(self):
        source = textwrap.dedent("""
            _log = logging.getLogger(__name__)
            _log.warning('x')
        """)
        sites = _walk(source)
        warn_sites = [s for s in sites if "warning" in s.snippet]
        assert warn_sites[0].pattern == PATTERN_LEGACY_LOG
        assert warn_sites[0].severity == SEV_LEGACY

    def test_log_var_no_assignment_falls_back_to_legacy(self):
        # Imported _log from elsewhere — no module-level assignment
        source = "_log.warning('x')\n"
        sites = _walk(source)
        assert sites[0].pattern == PATTERN_LEGACY_LOG


class TestCallsiteWalker:
    def test_records_class_context(self):
        source = textwrap.dedent("""
            class Foo:
                def bar(self):
                    self.log.info('x')
        """)
        sites = _walk(source)
        assert len(sites) == 1
        assert sites[0].pattern == PATTERN_SELF_LOG
        assert sites[0].enclosing_class == "Foo"
        assert sites[0].enclosing_function == "bar"

    def test_records_module_level_print(self):
        source = "print('top-level')\n"
        sites = _walk(source)
        assert len(sites) == 1
        assert sites[0].enclosing_class is None
        assert sites[0].enclosing_function is None

    def test_severity_assignment(self):
        source = textwrap.dedent("""
            print('x')
            self.log.info('y')
            logging.getLogger(__name__)
            _log.warning('z')
        """)
        sites = _walk(source)
        sev = {s.pattern: s.severity for s in sites}
        assert sev[PATTERN_PRINT] == SEV_SMELL
        assert sev[PATTERN_SELF_LOG] == SEV_GOOD
        assert sev[PATTERN_LOGGING_GETLOGGER] == SEV_BYPASS
        assert sev[PATTERN_LEGACY_LOG] == SEV_LEGACY

    def test_nested_classes(self):
        source = textwrap.dedent("""
            class Outer:
                class Inner:
                    def m(self):
                        self.log.info('x')
        """)
        sites = _walk(source)
        assert sites[0].enclosing_class == "Inner"

    def test_in_test_flag(self):
        source = "print('x')\n"
        sites = _walk(source, in_test=True)
        assert sites[0].in_test is True

    def test_snippet_captured(self):
        source = "self.log.info('hello world')\n"
        sites = _walk(source)
        assert "self.log.info" in sites[0].snippet


class TestCollectClasses:
    def test_class_inheriting_igorbase(self):
        source = "class Foo(IgorBase):\n    pass\n"
        tree = ast.parse(source)
        findings = _collect_classes(tree, "<test>", in_test=False)
        assert len(findings) == 1
        assert findings[0].inherits_igorbase is True

    def test_class_inheriting_agentbase(self):
        source = "class Foo(AgentBase):\n    pass\n"
        tree = ast.parse(source)
        findings = _collect_classes(tree, "<test>", in_test=False)
        assert findings[0].inherits_igorbase is True

    def test_class_with_no_inheritance_flagged(self):
        source = "class Foo:\n    pass\n"
        tree = ast.parse(source)
        findings = _collect_classes(tree, "<test>", in_test=False)
        # object base, no flags — actually class with no bases in our walker
        # is included because bases is empty (not all THIRD_PARTY)
        assert len(findings) == 1
        assert findings[0].inherits_igorbase is False

    def test_dataclass_exempt(self):
        source = textwrap.dedent("""
            @dataclass
            class Foo:
                x: int
        """)
        tree = ast.parse(source)
        findings = _collect_classes(tree, "<test>", in_test=False)
        assert findings == []

    def test_third_party_only_exempt(self):
        source = "class Foo(BaseModel):\n    pass\n"
        tree = ast.parse(source)
        findings = _collect_classes(tree, "<test>", in_test=False)
        assert findings == []

    def test_underscore_class_skipped(self):
        source = "class _Foo:\n    pass\n"
        tree = ast.parse(source)
        findings = _collect_classes(tree, "<test>", in_test=False)
        assert findings == []

    def test_transitive_inheritance(self):
        source = textwrap.dedent("""
            class Mid(IgorBase):
                pass

            class Child(Mid):
                pass
        """)
        tree = ast.parse(source)
        findings = _collect_classes(tree, "<test>", in_test=False)
        for cf in findings:
            assert cf.inherits_igorbase is True


class TestAggregate:
    def _file_result(self, callsites=None, classes=None, parse_error=None):
        from lab.claudecode.audit_logging import FileResult

        return FileResult(
            path="test.py",
            callsites=callsites or [],
            classes=classes or [],
            parse_error=parse_error,
        )

    def test_empty_aggregate(self):
        agg = aggregate([])
        assert agg["files_scanned"] == 0
        assert agg["pattern_counts"] == {}

    def test_pattern_counts(self):
        from lab.claudecode.audit_logging import Callsite

        cs = [
            Callsite("a", 1, PATTERN_PRINT, SEV_SMELL, None, None, False),
            Callsite("a", 2, PATTERN_PRINT, SEV_SMELL, None, None, False),
            Callsite("a", 3, PATTERN_SELF_LOG, SEV_GOOD, "C", "m", False),
        ]
        agg = aggregate([self._file_result(callsites=cs)])
        assert agg["pattern_counts"][PATTERN_PRINT] == 2
        assert agg["pattern_counts"][PATTERN_SELF_LOG] == 1
        assert agg["severity_counts"][SEV_SMELL] == 2

    def test_classes_missing_inh_excludes_tests(self):
        from lab.claudecode.audit_logging import ClassFinding

        classes = [
            ClassFinding("a.py", 1, "Prod", [], False, False),
            ClassFinding("test_a.py", 1, "TestClass", [], False, True),
        ]
        agg = aggregate([self._file_result(classes=classes)])
        assert len(agg["classes_missing_inh"]) == 1
        assert agg["classes_missing_inh"][0].name == "Prod"

    def test_parse_errors_collected(self):
        agg = aggregate([self._file_result(parse_error="bad syntax")])
        assert agg["files_scanned"] == 0
        assert len(agg["parse_errors"]) == 1


class TestGroupForMigration:
    def test_groups_by_pattern(self):
        from lab.claudecode.audit_logging import Callsite, ClassFinding

        cs = [
            Callsite("a.py", 1, PATTERN_PRINT, SEV_SMELL, None, None, False),
            Callsite("b.py", 5, PATTERN_LOGGING_GETLOGGER, SEV_BYPASS, "X", "f", False),
            Callsite("c.py", 9, PATTERN_LEGACY_LOG, SEV_LEGACY, None, None, False),
            Callsite("test_x.py", 1, PATTERN_PRINT, SEV_SMELL, None, None, True),
        ]
        agg = {
            "by_file": {
                "a.py": [cs[0]],
                "b.py": [cs[1]],
                "c.py": [cs[2]],
                "test_x.py": [cs[3]],
            },
            "classes_missing_inh": [
                ClassFinding("d.py", 1, "Foo", [], False, False),
            ],
        }
        groups = group_for_migration(agg)
        assert "a.py" in groups["print_smell"]
        assert "b.py" in groups["logging_bypass"]
        assert "c.py" in groups["legacy_log"]
        # Test files excluded from migration buckets
        assert "test_x.py" not in groups["print_smell"]
        assert "d.py" in groups["missing_inheritance"]


class TestAuditFileCliExemption:
    def test_cli_main_print_suppressed(self, tmp_path):
        f = tmp_path / "tool.py"
        f.write_text(textwrap.dedent("""
            def main():
                print('cli output')

            if __name__ == '__main__':
                main()
        """))
        # Move file under REPO_ROOT for relative_to to work
        import lab.claudecode.audit_logging as al

        repo = al.REPO_ROOT
        target = repo / "lab" / "claudecode" / "_test_cli_tmp.py"
        target.write_text(f.read_text())
        try:
            result = al.audit_file(target)
            # print() inside main() of CLI entrypoint is suppressed
            patterns = [c.pattern for c in result.callsites]
            assert PATTERN_PRINT not in patterns
        finally:
            target.unlink()

    def test_non_cli_print_kept(self, tmp_path):
        import lab.claudecode.audit_logging as al

        repo = al.REPO_ROOT
        target = repo / "lab" / "claudecode" / "_test_noncli_tmp.py"
        target.write_text("def helper():\n    print('debris')\n")
        try:
            result = al.audit_file(target)
            patterns = [c.pattern for c in result.callsites]
            assert PATTERN_PRINT in patterns
        finally:
            target.unlink()
