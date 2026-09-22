"""The model seam: reading a reply, and the two shipped transports.

None of this needs a model, a key or a network. The one test that runs a real
process runs ``sys.executable``, which is on every machine that can run the suite
at all -- and it asserts what the process received on stdin, because "the prompt
reached the model" is otherwise an unexamined assumption.
"""

from __future__ import annotations

import shlex
import sys

import pytest

from deskhand.errors import ModelFailed
from deskhand.model import (
    ENV_COMMAND,
    ENV_TIMEOUT,
    SEPARATOR,
    CommandModel,
    FakeModel,
    Model,
    ModelCall,
    model_from_env,
    parse_object,
)


class TestParseObject:
    def test_a_bare_object_is_read(self) -> None:
        assert parse_object('{"verb": "WAIT"}', what="t") == {"verb": "WAIT"}

    def test_a_fenced_object_is_read(self) -> None:
        reply = 'here you go:\n```json\n{"ok": true}\n```\nhope that helps'
        assert parse_object(reply, what="t") == {"ok": True}

    def test_a_fence_without_a_language_tag_is_read(self) -> None:
        assert parse_object('```\n{"ok": true}\n```', what="t") == {"ok": True}

    def test_braces_in_prose_do_not_confuse_it(self) -> None:
        reply = 'I considered {this} and {that}, then: {"verb": "WAIT"}'
        assert parse_object(reply, what="t") == {"verb": "WAIT"}

    def test_braces_inside_a_string_do_not_confuse_it(self) -> None:
        assert parse_object('{"why": "press {x} now"}', what="t") == {"why": "press {x} now"}

    def test_nesting_is_kept_whole(self) -> None:
        reply = 'noise {"a": {"b": {"c": 1}}, "d": [{"e": 2}]} noise'
        assert parse_object(reply, what="t") == {"a": {"b": {"c": 1}}, "d": [{"e": 2}]}

    def test_the_same_object_twice_is_accepted(self) -> None:
        """A model that fences its answer and then repeats it has still said one thing."""
        reply = '```json\n{"verb": "PRESS"}\n```\nIn short: {"verb": "PRESS"}'
        assert parse_object(reply, what="t") == {"verb": "PRESS"}

    def test_a_nested_object_is_not_counted_as_two(self) -> None:
        assert parse_object('noise {"a": {"b": 1}} noise', what="t") == {"a": {"b": 1}}

    def test_one_object_inside_an_array_is_read_liberally(self) -> None:
        """Documented, not accidental: the container is tolerated, a disagreement is not."""
        assert parse_object('[{"verb": "WAIT"}]', what="t") == {"verb": "WAIT"}

    @pytest.mark.parametrize(
        "reply",
        [
            # a decoy, or a draft, stated before the real answer
            'Here is one: {"verb": "WAIT"}\nActually: {"verb": "PRESS", "target": "go_button"}',
            # the model echoing the format example, then giving its answer in prose
            'Format example:\n```json\n{"verb": "PRESS", "target": "go_button"}\n```'
            '\nMy answer: {"verb": "WAIT"}',
            # two fenced blocks: a draft, then the answer
            '```json\n{"verb": "WAIT"}\n```\nReal:\n```json\n{"verb": "PRESS"}\n```',
        ],
    )
    def test_a_reply_with_two_different_objects_is_refused(self, reply: str) -> None:
        """A decoy is valid JSON, so choosing one silently chooses a real action.

        Each of these used to be *executed*: taking the first object meant a draft, an
        echoed format example, or a decoy was acted on instead of the answer. Refusing is
        the only safe reading, and the reason is fed back into the next decision.
        """
        with pytest.raises(ModelFailed, match="2 different JSON objects"):
            parse_object(reply, what="t")

    @pytest.mark.parametrize(
        "reply",
        ["", "   ", "I could not do that.", "[1, 2, 3]", "42", '{"verb": "WAIT"', "null"],
    )
    def test_a_reply_with_no_object_is_refused(self, reply: str) -> None:
        with pytest.raises(ModelFailed, match="no JSON object"):
            parse_object(reply, what="t")

    def test_the_failure_names_the_question_and_shows_the_reply(self) -> None:
        with pytest.raises(ModelFailed) as caught:
            parse_object("I would rather not", what="decider reply 3")
        assert "decider reply 3" in str(caught.value)
        assert "I would rather not" in str(caught.value)

    def test_a_very_long_reply_is_truncated_in_the_message(self) -> None:
        with pytest.raises(ModelFailed) as caught:
            parse_object("x" * 5000, what="t")
        assert len(str(caught.value)) < 300


class TestFakeModel:
    def test_it_records_what_it_was_asked(self) -> None:
        model = FakeModel(['{"ok": true}'])
        assert model.ask(system="S", prompt="P") == {"ok": True}
        assert model.asked == 1
        assert model.calls == [ModelCall(system="S", prompt="P")]

    def test_a_call_keeps_both_halves_where_a_test_can_read_them(self) -> None:
        model = FakeModel(['{"ok": true}'])
        model.ask(system="SYSTEM", prompt='{"a": 1}')
        call = model.calls[0]
        assert call.text == f"SYSTEM{SEPARATOR}" + '{"a": 1}'
        assert call.payload() == {"a": 1}

    def test_a_mapping_reply_is_passed_through_unparsed(self) -> None:
        model = FakeModel([{"ok": True}])
        assert model.ask(system="s", prompt="p") == {"ok": True}

    def test_a_scripted_exception_is_raised(self) -> None:
        model = FakeModel([ModelFailed("the network is gone")])
        with pytest.raises(ModelFailed, match="the network is gone"):
            model.ask(system="s", prompt="p")

    def test_running_out_is_a_failure_rather_than_a_default_answer(self) -> None:
        model = FakeModel(['{"ok": true}'])
        model.ask(system="s", prompt="p")
        with pytest.raises(ModelFailed, match="ran out after 2 question"):
            model.ask(system="s", prompt="p")

    def test_it_is_a_model(self) -> None:
        assert isinstance(FakeModel([]), Model)


class TestModelFromEnv:
    def test_no_command_is_an_error_that_says_how_to_set_one(self) -> None:
        with pytest.raises(ModelFailed) as caught:
            model_from_env({})
        message = str(caught.value)
        assert ENV_COMMAND in message
        assert "stdin" in message

    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_a_blank_command_is_the_same_error(self, blank: str) -> None:
        with pytest.raises(ModelFailed, match=ENV_COMMAND):
            model_from_env({ENV_COMMAND: blank})

    def test_it_returns_a_transport_named_after_the_command(self) -> None:
        model = model_from_env({ENV_COMMAND: "ollama run llama3.1"})
        assert isinstance(model, CommandModel)
        assert model.name == "command:ollama run llama3.1"

    def test_the_timeout_comes_from_the_environment(self) -> None:
        model = model_from_env({ENV_COMMAND: "x", ENV_TIMEOUT: "3.5"})
        assert isinstance(model, CommandModel)
        assert model.timeout_s == 3.5

    def test_a_blank_timeout_falls_back_to_the_default(self) -> None:
        model = model_from_env({ENV_COMMAND: "x", ENV_TIMEOUT: "  "})
        assert isinstance(model, CommandModel)
        assert model.timeout_s == 120.0

    def test_a_timeout_that_is_not_a_number_is_refused(self) -> None:
        with pytest.raises(ModelFailed, match=ENV_TIMEOUT):
            model_from_env({ENV_COMMAND: "x", ENV_TIMEOUT: "soon"})


def command(code: str) -> CommandModel:
    """A real command, quoted for the shell the same way a user would write it."""
    return CommandModel(command=shlex.join([sys.executable, "-c", code]), timeout_s=20.0)


class TestCommandModel:
    def test_it_runs_the_command_and_reads_the_object(self) -> None:
        model = command("import json; print(json.dumps({'ok': True, 'how': 'read it'}))")
        assert model.ask(system="s", prompt="p") == {"ok": True, "how": "read it"}

    def test_the_system_text_and_the_prompt_reach_stdin(self) -> None:
        model = command(
            "import json, sys; text = sys.stdin.read(); "
            "print(json.dumps({'len': len(text), 'has_prompt': 'PAYLOAD' in text}))"
        )
        reply = model.ask(system="SYSTEM", prompt="PAYLOAD")
        assert reply == {"len": len("SYSTEM") + len(SEPARATOR) + len("PAYLOAD"), "has_prompt": True}

    def test_a_reply_that_is_prose_around_json_still_works(self) -> None:
        model = command(r"print('sure: {\"ok\": true} done')")
        assert model.ask(system="s", prompt="p") == {"ok": True}

    def test_a_failing_command_reports_the_exit_code_and_the_stderr(self) -> None:
        model = command("import sys; print('the key is wrong', file=sys.stderr); sys.exit(3)")
        with pytest.raises(ModelFailed) as caught:
            model.ask(system="s", prompt="p")
        assert "exited 3" in str(caught.value)
        assert "the key is wrong" in str(caught.value)

    def test_a_command_that_is_not_on_the_path_is_reported(self) -> None:
        model = CommandModel(command="deskhand-no-such-program-xyz", timeout_s=5.0)
        with pytest.raises(ModelFailed, match="not on PATH"):
            model.ask(system="s", prompt="p")

    def test_a_command_that_never_answers_is_cut_off(self) -> None:
        # signal.pause() blocks until a signal arrives, so the process is still
        # waiting when the timeout fires -- without a sleep in the test itself.
        model = CommandModel(
            command=shlex.join([sys.executable, "-c", "import signal; signal.pause()"]),
            timeout_s=0.1,
        )
        with pytest.raises(ModelFailed, match=r"did not answer within 0\.1s"):
            model.ask(system="s", prompt="p")

    def test_a_command_that_answers_with_prose_is_refused(self) -> None:
        model = command("print('I am not going to answer that')")
        with pytest.raises(ModelFailed, match="no JSON object"):
            model.ask(system="s", prompt="p")

    def test_it_is_a_model(self) -> None:
        assert isinstance(command("pass"), Model)
