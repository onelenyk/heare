"""The hands' terminal: the ACP wire as a person reads it."""

from __future__ import annotations

from heare.reach.terminal import OUTPUT_LINES, Terminal


def _update(**update) -> dict:
    return {"method": "session/update", "params": {"update": update}}


def test_a_piece_of_work_reads_as_asked_ran_saw_said():
    screen = Terminal()
    screen.begin("запусти тести", new=True)
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
        "› запусти тести",
        "агент: Запускаю.",
        "$ make test",
        "  1073 passed",
        "агент: Усе пройшло.",
    ]


def test_new_work_is_set_apart_and_work_that_goes_on_is_not():
    screen = Terminal()
    screen.begin("знайди файл", new=True)
    screen.begin("видали його", new=False)
    screen.begin("список покупок", new=True)

    assert screen.text().splitlines() == [
        "› знайди файл", "› видали його", "", "──── нова справа ────",
        "› список покупок",
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
    assert screen.written == 400
    assert len(screen.lines) < 400

