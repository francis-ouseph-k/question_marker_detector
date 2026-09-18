"""Question list loading and validation (TD §11, Requirements §2.2)."""

import json

import pytest

from qmd.errors import QuestionListError
from qmd.question_list import build_question_list, load_question_list


def test_hierarchy_and_targets(multilevel):
    q = multilevel.questions
    assert q["10(b)(1)"].is_target
    assert q["10(b)"].is_target            # explicitly allowed
    assert q["4(a)"].is_target
    assert q["5(a)(ii)"].segment_types == ["NUM", "ALPHA", "ROMAN"]
    assert q["5(a)(i)"].segment_types == ["NUM", "ALPHA", "ROMAN"]
    assert q["4(a)"].segment_types == ["NUM", "ALPHA"]


def test_parent_is_not_target_by_default():
    ql = build_question_list(["10(a)", "10", "11"])
    assert not ql.questions["10"].is_target
    assert ql.questions["10(a)"].is_target


def test_alternative_separators_become_canonical():
    ql = build_question_list(["10.b.1", "10-b-2"])
    assert set(ql.questions) == {"10(b)(1)", "10(b)(2)"}


def test_duplicate_rejected():
    with pytest.raises(QuestionListError, match="Duplicate"):
        build_question_list(["10(b)(1)", "10.b.1"])


def test_flattened_collision_marked_confusable():
    ql = build_question_list(["1(1)", "11", "2"])
    assert ql.questions["11"].confusable_with == {"1(1)"}
    assert ql.questions["1(1)"].confusable_with == {"11"}
    assert not ql.questions["2"].confusable_with


@pytest.mark.parametrize("bad", ["", "a1", "Q1", "10(bb)", "10#b"])
def test_invalid_ids(bad):
    with pytest.raises(QuestionListError):
        build_question_list([bad])


def test_empty_list():
    with pytest.raises(QuestionListError):
        build_question_list([])


def test_load_yaml_json_txt(tmp_path):
    y = tmp_path / "q.yaml"
    y.write_text("exam_id: E1\nversion: v2\nquestions: ['1', '2']\nanswering_rules: {min_answers: 1, max_answers: 2}\n")
    ql = load_question_list(y)
    assert ql.version_label == "E1:v2" and ql.rules.max_answers == 2

    j = tmp_path / "q.json"
    j.write_text(json.dumps({"questions": ["1", {"id": "2(a)"}]}))
    ql = load_question_list(j)
    assert "2(a)" in ql.questions and ql.version.startswith("sha256:")

    t = tmp_path / "q.txt"
    t.write_text("# comment\n1\n\n2  # second\n")
    assert set(load_question_list(t).questions) == {"1", "2"}


def test_bad_rules(tmp_path):
    with pytest.raises(QuestionListError):
        build_question_list(["1"], rules={"min_answers": 3, "max_answers": 1})


def test_unsupported_format(tmp_path):
    p = tmp_path / "q.csv"
    p.write_text("1\n")
    with pytest.raises(QuestionListError):
        load_question_list(p)


def test_non_ascii_digits_rejected():
    with pytest.raises(QuestionListError):
        build_question_list(["१"])
