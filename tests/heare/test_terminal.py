"""The hands' terminal: the ACP wire as a person reads it."""

from __future__ import annotations

import time

from heare.reach.terminal import OUTPUT_LINES, Terminal

AT = time.mktime((2026, 10, 24, 14, 32, 0, 0, 0, -1))


def _update(**update) -> dict:
    return {"method": "session/update", "params": {"update": update}}


def test_a_piece_of_work_reads_as_asked_ran_saw_said():
    screen = Terminal()
    screen.begin("Завдання: запусти всі тести проєкту…", new=True,
                 asked="запусти тести", at=AT)
    screen.feed("in", _update(sessionUpdate="agent_message_chunk",
                              content={"text": "Запускаю."}))
    screen.feed("in", _update(sessionUpdate="tool_call", toolCallId="1", title="bash"))
    screen.feed("in", _update(sessionUpdate="tool_call_update", toolCallId="1",
                              rawInput={"command": "make test"}))
    screen.feed("in", _update(sessionUpdate="tool_call_update", toolCallId="1",
                              status="completed",
                              content=[{"type": "content",
                                        "content": {"type": "text", "text": "1073 passed"}}]))
    screen.feed("in", _update(sessionUpdate="agent_message_chunk",
                              content={"text": "Усе пройшло."}))
    screen.end("done", "Усе пройшло.")

    assert screen.text().splitlines() == [
        "14:32 › запусти тести",
        "агент: Запускаю.",
        "$ make test",
        "  1073 passed",
        "агент: Усе пройшло.",
    ]


def test_new_work_is_set_apart_and_work_that_goes_on_is_not():
    screen = Terminal()
    screen.begin("знайди файл", new=True, at=AT)
    screen.end("done", "")
    screen.begin("видали його", new=False, at=AT)
    screen.end("done", "")
    screen.begin("список покупок", new=True, at=AT)
    screen.end("done", "")

    assert screen.text().splitlines() == [
        "14:32 › знайди файл", "14:32 › видали його", "", "──── нова справа ────",
        "14:32 › список покупок",
    ]


def test_a_call_that_never_says_what_it_runs_is_shown_by_its_title_once():
    screen = Terminal()
    screen.feed("in", _update(sessionUpdate="tool_call", toolCallId="1", title="Пошук у вебі"))
    screen.feed("in", _update(sessionUpdate="tool_call_update", toolCallId="1",
                              status="completed"))
    screen.feed("in", _update(sessionUpdate="tool_call", toolCallId="2", title="read",
                              rawInput={"file_path": "a.txt"}))
    screen.feed("in", _update(sessionUpdate="tool_call_update", toolCallId="2",
                              title="read", rawInput={"file_path": "a.txt"}))

    assert screen.text().splitlines() == ["• Пошук у вебі", "• read a.txt"]


def test_long_output_is_cut_and_says_how_much_is_left():
    screen = Terminal()
    out = "\n".join(f"рядок {i}" for i in range(OUTPUT_LINES + 4))
    screen.feed("in", _update(sessionUpdate="tool_call_update", toolCallId="1",
                              rawInput={"command": "ls"}, status="completed",
                              rawOutput={"output": out}))

    lines = screen.text().splitlines()
    assert lines[0] == "$ ls"
    assert len(lines) == 1 + OUTPUT_LINES + 1
    assert lines[-1] == "  … ще 4 рядків"


def test_thinking_leaked_into_the_answer_and_our_own_messages_are_left_out():
    screen = Terminal()
    screen.feed("out", _update(sessionUpdate="agent_message_chunk", content={"text": "ні"}))
    screen.feed("in", {"method": "session/prompt"})
    screen.feed("in", _update(sessionUpdate="agent_message_chunk",
                              content={"text": "<think>хм</think>Готово."}))
    screen.end("stopped", "Зупинено.")

    assert screen.text().splitlines() == ["агент: Готово.", "■ зупинено"]


def test_every_line_written_counts_even_when_the_screen_is_full():
    screen = Terminal()
    for i in range(400):
        screen.begin(f"справа {i}", new=False)
        screen.end("done", "")
    assert screen.written == 400
    assert len(screen.lines) < 400



def test_while_work_runs_what_the_agent_says_so_far_shows_and_that_it_works():
    screen = Terminal()
    screen.begin("скільки місця", new=True, at=AT)
    screen.feed("in", _update(sessionUpdate="agent_message_chunk",
                              content={"text": "Дивлюсь"}))

    assert screen.text().splitlines()[-2:] == ["агент: Дивлюсь", "⋯ працює"]
    screen.end("done", "")
    assert screen.text().splitlines()[-1] == "агент: Дивлюсь"


def test_a_command_shows_one_line_without_the_agents_noise():
    screen = Terminal()
    screen.feed("in", _update(
        sessionUpdate="tool_call", toolCallId="1",
        rawInput={"command": "export CI=true PAGER=cat; GIT_PAGER=cat; cd ~/x && ls"}))
    screen.feed("in", _update(
        sessionUpdate="tool_call", toolCallId="2",
        rawInput={"command": "export CI=true PAGER=cat; python3 - <<'EOF'\nimport os\nEOF"}))

    assert screen.text().splitlines() == [
        "$ cd ~/x && ls", "$ python3 - <<'EOF' …"]


def test_a_refusal_reads_as_the_persons_no_and_not_as_an_error():
    screen = Terminal()
    screen.permit("bash python3 - <<'EOF'\nimport os", allowed=False)
    screen.feed("in", _update(
        sessionUpdate="tool_call_update", toolCallId="1", status="failed",
        content=[{"type": "content", "content": {
            "type": "text",
            "text": "The user rejected permission to use this specific tool call."}}]))

    assert screen.text().splitlines() == ["  ? дозволити: bash python3 - <<'EOF' … → ні"]


def test_a_question_waiting_for_the_person_shows_until_it_is_answered():
    screen = Terminal()
    screen.begin("опублікуй", new=True, at=AT)
    screen.permit("git push", None)
    assert screen.text().splitlines()[-1] == "  ? чекає дозволу: git push"
    screen.permit("git push", True)
    assert screen.text().splitlines()[-2:] == ["  ? дозволити: git push → так", "⋯ працює"]



def test_what_the_gate_lets_through_reads_as_done_on_its_own_and_why():
    screen = Terminal()
    screen.begin("прибери дублікати", new=True, at=AT)
    screen.permit("trash a.pdf", None, "judge")
    assert screen.text().splitlines()[-1] == "  ? суддя зважує: trash a.pdf"
    screen.permit("trash a.pdf", True, "judge", "прибирає дублікат, як просив")
    screen.permit("git push", False, "person", "це побачать інші")
    screen.end("done", "")

    assert screen.text().splitlines()[1:] == [
        "  ✓ сам: прибирає дублікат, як просив",
        "  ? дозволити: git push (питаю, бо це побачать інші) → ні",
    ]
