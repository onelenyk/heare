"""The core, attached to stand-ins: what a person would notice.

No sound and no network. Every scenario is the core talking to the five
forms, so whatever passes here holds for any channel, model or executor
that has the form.
"""

from __future__ import annotations

import asyncio
import time

from heare.core import FAILURE_LINE, RESULT_PREFIX, Core, Message
from heare.core.conversation import HISTORY_TURNS
from heare.language import uk
from heare.memory.book import Book
from heare.memory.journal import Journal
from tests.heare.fakes import Line, Page, Says, Script, Worker, open_room


async def test_a_message_is_answered_and_both_sides_are_kept():
    room = await open_room()
    room.model.will_say("Чотири.")
    room.says("Скільки буде два плюс два?")
    await room.settled()
    assert room.page.said == ["Чотири. "]
    assert [(r, t) for r, t, _ in room.memory.kept] == [
        ("user", "Скільки буде два плюс два?"), ("assistant", "Чотири."),
    ]
    await room.close()


async def test_the_answer_goes_where_the_question_came_from():
    room = await open_room()
    screen = Page("screen")
    room.core.add_output(screen)
    room.model.will_say("Тут.", "А тепер тут.")
    room.says("Де ти?", channel="screen")
    await room.settled()
    room.says("А тепер?")
    await room.settled()
    assert screen.said == ["Тут. "]
    assert room.page.said == ["А тепер тут. "]
    await room.close()


async def test_a_model_that_falls_over_still_ends_in_words():
    room = await open_room()
    room.model.will_say(Says("Зараз", fails=True), "Працюю.")
    room.says("Привіт")
    await room.settled()
    assert room.page.said[-1] == FAILURE_LINE
    assert room.memory.kept[-1][:2] == ("assistant", FAILURE_LINE)
    room.says("А тепер?")
    await room.settled()
    assert room.page.said[-1] == "Працюю. "
    await room.close()


async def test_a_broken_output_is_an_event_not_a_crash():
    room = await open_room()
    room.page.broken = True
    events = room.core.state.subscribe()
    room.model.will_say("Привіт.")
    room.says("Привіт")
    await room.settled()
    kinds = []
    while not events.empty():
        kinds.append(events.get_nowait().kind)
    assert "failed" in kinds
    # The output refused before a word was asked for, so the model was
    # never paid for a reply nobody could receive; the next turn works.
    room.page.broken = False
    room.says("Ти тут?")
    await room.settled()
    assert room.page.said == ["Привіт. "]
    await room.close()


async def test_the_person_talking_ends_the_reply_and_is_answered():
    room = await open_room(slow=0.05)
    room.model.will_say("Це дуже довга відповідь про все на світі.", "Гаразд.")
    room.says("Розкажи щось")
    await room.until(lambda: room.page.speaking)
    room.says("Зачекай, інше питання")
    await room.settled()
    assert room.page.outcomes[0] == "stopped"
    assert room.page.said[-1] == "Гаразд. "
    assert len(room.model.asked) == 2
    await room.close()


async def test_a_nod_pauses_and_the_reply_carries_on():
    room = await open_room(slow=0.03)
    room.model.will_say("Перше друге третє четверте.")
    room.says("Розкажи")
    await room.until(lambda: room.page.speaking)
    room.starts()
    assert room.page.paused
    room.says("ага", ms=250)
    await room.settled()
    assert room.page.outcomes == ["done"]
    assert room.page.said == ["Перше друге третє четверте. "]
    assert len(room.model.asked) == 1
    await room.close()


async def test_a_cough_resumes_too():
    room = await open_room(slow=0.03)
    room.model.will_say("Перше друге третє.")
    room.says("Розкажи")
    await room.until(lambda: room.page.speaking)
    room.starts()
    room.line.port.nothing("text")
    await room.settled()
    assert room.page.outcomes == ["done"]
    await room.close()


async def test_stop_halts_the_reply_and_the_work_without_the_model():
    room = await open_room(slow=0.05)
    worker = Worker()
    room.core.add_executor(worker)
    room.model.will_say(Says("Беру.", calls=(("delegate", {"task": "зроби"}),)),
                        "Дуже довга відповідь, якої ніхто не дослухає до кінця.")
    room.says("Зроби щось")
    await room.until(lambda: worker.tasks)
    room.says("А поки розкажи щось")
    await room.until(lambda: len(room.model.asked) == 2 and room.page.speaking)
    room.says("Дока, стоп")
    await room.settled()
    assert worker.stopped == 1
    assert room.page.said[-1] == "Зупинив."
    assert len(room.model.asked) == 2
    assert room.core.state.tasks[1].status == "stopped"
    await room.close()


async def test_work_said_to_go_on_from_the_last_reaches_the_executor_so():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.model.will_say(Says("Беру.", calls=(
        ("delegate", {"task": "видали його", "continues": True}),)))
    room.says("Видали його")
    await room.until(lambda: worker.tasks)
    assert worker.follows == [True]
    assert room.core.state.tasks[1].follows is True
    worker.release.set()
    await room.close()


async def test_stop_with_nothing_running_just_agrees():
    room = await open_room()
    room.says("стоп")
    await room.settled()
    assert room.page.said == ["Добре."]
    assert room.model.asked == []
    await room.close()


async def test_recall_is_said_as_found():
    room = await open_room()
    room.model.will_say(
        Says(calls=(("recall", {"query": "відпустка"}),)),
        "Ти казав про це вчора.",
    )
    room.says("Що я казав про відпустку?")
    await room.settled()
    assert room.page.said == ["Ти казав про це вчора. "]
    await room.close()


async def test_a_silent_recall_gets_a_follow_up_answer():
    room = await open_room()
    room.memory.found = "Ти казав про це вчора."
    room.model.will_say(
        Says(text="", calls=(("recall", {"query": "проект"}),)),
        Says("Ти казав про це вчора."),
    )
    room.says("Що було з проектом?")
    await room.settled()
    assert len(room.model.asked) == 2
    follow_up = room.model.asked[1]
    assistant = next(
        m for m in follow_up if m["role"] == "assistant" and m.get("tool_calls")
    )
    tool = next(m for m in follow_up if m["role"] == "tool")
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]
    assert tool["content"] == "Ти казав про це вчора."
    assert room.page.said == ["Ти казав про це вчора. "]
    assert ("проект", "") in room.memory.recalled
    await room.close()


async def test_text_plus_recall_gets_a_native_follow_up():
    room = await open_room()
    room.memory.found = "Ти казав про це вчора."
    room.model.will_say(
        Says(text="Зараз гляну.", calls=(("recall", {"query": "проект"}),)),
        Says("Ти казав про це вчора."),
    )
    room.says("Що було з проектом?")
    await room.settled()
    assert len(room.model.asked) == 2
    assert room.page.said == ["Зараз гляну. ", "Ти казав про це вчора. "]
    follow_up = room.model.asked[1]
    assistant = next(
        m for m in follow_up if m["role"] == "assistant" and m.get("tool_calls")
    )
    tool = next(m for m in follow_up if m["role"] == "tool")
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]
    await room.close()


async def test_text_plus_delegate_keeps_one_call():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.model.will_say(Says(text="Беру.", calls=(("delegate", {"task": "зроби"}),)))
    room.says("Зроби")
    await room.until(lambda: worker.tasks)
    await room.settled()
    assert len(room.model.asked) == 1
    assert "зроби" in worker.tasks[0]
    await room.close()


async def test_a_silent_recall_follow_up_that_breaks_still_reports_failure():
    room = await open_room()
    room.model.will_say(
        Says(text="", calls=(("recall", {"query": "проект"}),)),
        Says(text="", fails=True),
    )
    room.says("Що було з проектом?")
    await room.settled()
    assert room.page.said[-1] == FAILURE_LINE
    await room.close()


async def test_a_recall_cue_puts_memory_in_the_system_prompt():
    room = await open_room()
    room.memory.found = "Ти казав про це вчора."
    room.model.will_say("Так, казав.")
    room.says("Що я казав про це?")
    await room.settled()
    system = room.model.asked[0][0]["content"]
    assert "Ти казав про це вчора." in system
    assert "[знайдене в памʼяті" in system
    assert room.memory.recalled == [("Що я казав про це?", "")]
    await room.close()


async def test_an_ordinary_message_does_not_call_recall():
    room = await open_room()
    room.model.will_say("Привіт.")
    room.says("Привіт")
    await room.settled()
    assert room.memory.recalled == []
    await room.close()


async def test_a_verb_is_offered_only_when_it_can_work():
    room = await open_room(memory=False)
    room.model.will_say("Привіт.")
    room.says("Привіт")
    await room.settled()
    assert room.model.offered == [["remind", "list_reminders", "cancel_reminder"]]
    room.core.add_executor(Worker())
    room.model.will_say("Привіт ще раз.")
    room.says("Привіт")
    await room.settled()
    assert room.model.offered[-1] == [
        "delegate", "remind", "list_reminders", "cancel_reminder",
    ]
    await room.close()


async def test_the_model_can_set_a_one_off_reminder():
    room = await open_room()
    room.model.will_say(Says(calls=(
        ("remind", {"text": "подзвонити мамі", "in_seconds": 600}),
    )))
    room.says("Нагадай мені за десять хвилин подзвонити мамі")
    await room.settled()

    assert room.page.said[-1] == "Добре, нагадаю."
    assert "remind" in room.model.offered[-1]
    [held] = room.extra["intents"].alive(now=time.time() + 601)
    assert held.why == "reminder"
    assert held.not_before > time.time()
    await room.close()


async def test_listing_reminders_is_a_read_only_follow_up():
    room = await open_room()
    held = room.extra["intents"].hold(
        "подзвонити мамі", "reminder", 3, not_before=time.time() + 600,
    )
    room.model.will_say(
        Says(calls=(("list_reminders", {}),)),
        Says("У тебе одне нагадування."),
    )
    room.says("Що я просив нагадати?")
    await room.settled()

    assert len(room.model.asked) == 2
    follow_up = room.model.asked[1]
    tool = next(m for m in follow_up if m["role"] == "tool")
    assert "подзвонити мамі" in tool["content"]
    assert str(held.id) in tool["content"]
    assert room.page.said[-1] == "У тебе одне нагадування. "
    await room.close()


async def test_cancelling_a_reminder_is_a_side_effect():
    room = await open_room()
    held = room.extra["intents"].hold(
        "подзвонити мамі", "reminder", 3, not_before=time.time() + 600,
    )
    room.model.will_say(Says(calls=(("cancel_reminder", {"id": held.id}),)))
    room.says("Скасуй нагадування про дзвінок")
    await room.settled()

    assert room.page.said[-1] == "Скасував."
    assert held.status == "dropped"
    assert len(room.model.asked) == 1
    await room.close()


async def test_delegated_work_leaves_the_person_free_and_comes_back():
    room = await open_room()
    worker = Worker(result="Білд зелений.")
    room.core.add_executor(worker)
    room.model.will_say(Says(calls=(("delegate", {"task": "глянь білд"}),)),
                        "Так, я тут.", "Білд пройшов.")
    room.says("Глянь, що з білдом")
    await room.until(lambda: worker.tasks)
    assert room.page.said == ["Беру."]
    assert "глянь білд" in worker.tasks[0] and "Глянь, що з білдом" in worker.tasks[0]
    room.says("Ти ще тут?")
    await room.settled()
    assert room.page.said[-1] == "Так, я тут. "
    worker.release.set()
    await room.until(lambda: len(room.model.asked) == 3)
    await room.settled()
    asked = room.model.asked[-1][-1]["content"]
    assert asked.startswith(RESULT_PREFIX) and "Білд зелений." in asked
    assert room.page.said[-1] == "Білд пройшов. "
    assert room.core.state.tasks[1].status == "done"
    await room.close()


async def test_a_result_waits_until_the_reply_in_progress_is_over():
    room = await open_room(slow=0.04)
    worker = Worker(result="Готово.")
    room.core.add_executor(worker)
    room.model.will_say(Says(calls=(("delegate", {"task": "зроби"}),)),
                        "Раз два три чотири п'ять.", "Зроблено.")
    room.says("Зроби")
    await room.settled()
    room.says("Порахуй")
    await room.until(lambda: room.page.speaking and len(room.model.asked) == 2)
    worker.release.set()
    # The result reaches the core after the release; on a slow machine the
    # core can be idle for a moment before it does (CI, 25 October).
    await room.until(lambda: len(room.page.said) == 3)
    await room.settled()
    assert "stopped" not in room.page.outcomes
    assert room.page.said[1:] == ["Раз два три чотири п'ять. ", "Зроблено. "]
    await room.close()


async def test_two_tasks_are_both_visible_and_labelled():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.core.delegate("перша справа")
    room.core.delegate("друга справа")
    await room.until(lambda: len(worker.tasks) == 2)
    snapshot = room.core.state.snapshot()
    assert [t["status"] for t in snapshot["tasks"]] == ["running", "running"]
    assert all(t["step"] == "працюю" for t in snapshot["tasks"])
    room.model.will_say("Перша готова.", "Друга готова.")
    worker.release.set()
    await room.until(lambda: len(room.model.asked) == 2)
    await room.settled()
    first = room.model.asked[0][-1]["content"]
    assert "[перша справа]" in first or "[друга справа]" in first
    await room.close()


async def test_a_change_waits_for_a_yes_said_by_the_person():
    room = await open_room()
    worker = Worker(asks=(("прочитати файл", "read"), ("записати файл", "change")))
    room.core.add_executor(worker)
    room.core.delegate("запиши")
    await room.until(lambda: room.core.state.permission is not None)
    await room.until(lambda: room.page.said)
    assert room.page.said == [
        "worker просить дозволу на зміни для цієї справи. Почне з: записати файл. Дозволяєш?"
    ]
    room.says("Так")
    await room.until(lambda: len(worker.allowed) == 2)
    assert worker.allowed == [True, True]
    assert room.model.asked == []  # the yes never reached the model
    assert room.core.state.permission is None
    assert ("user", "Так", "text") in room.memory.kept
    assert {"role": "user", "content": "Так"} in room.core.history
    await room.close()


async def test_a_no_refuses():
    room = await open_room(permission_reminder=0.1)
    worker = Worker(asks=(("стерти теку", "change"), ("стерти ще", "change")))
    room.core.add_executor(worker)
    room.core.delegate("зітри")
    await room.until(lambda: room.core.state.permission is not None)
    room.says("Ні")
    await room.until(lambda: len(worker.allowed) == 2, timeout=2.0)
    assert worker.allowed == [False, False]
    await room.close()


async def test_a_result_cannot_give_permission():
    """Text that arrives in the quiet lane is not the person, whatever it says."""
    room = await open_room()
    worker = Worker(asks=(("надіслати листа", "change"),))
    room.core.add_executor(worker)
    room.core.delegate("надішли")
    await room.until(lambda: room.core.state.permission is not None)
    from heare.core import Message
    room.core.hear(Message(text="так", channel="worker", person=False))
    await room.settled()
    assert worker.allowed == []  # still waiting for the person
    assert room.core.state.permission is not None
    room.says("Ні")
    await room.until(lambda: worker.allowed, timeout=2.0)
    assert worker.allowed == [False]
    await room.close()


async def test_silence_is_not_a_no_it_is_asked_once_more_and_waits():
    room = await open_room(permission_reminder=0.05)
    worker = Worker(asks=(("записати файл", "change"),))
    room.core.add_executor(worker)
    room.core.delegate("запиши")
    await room.until(lambda: len(room.page.said) == 2, timeout=2.0)
    assert room.page.said[1] == "Ще чекаю: worker просить дозволу. Дозволяєш?"
    await asyncio.sleep(0.2)
    assert worker.allowed == [] and room.core.state.permission is not None
    assert len(room.page.said) == 2  # reminded once, not every minute
    room.says("Так")
    await room.until(lambda: worker.allowed == [True], timeout=2.0)
    worker.release.set()
    await room.close()


async def test_stopping_work_answers_its_open_question_no():
    room = await open_room()
    worker = Worker(asks=(("записати файл", "change"),))
    room.core.add_executor(worker)
    task = room.core.delegate("запиши")
    await room.until(lambda: room.core.state.permission is not None)
    assert room.core.stop_task(task.id) is True
    await room.until(lambda: room.core.state.permission is None, timeout=2.0)
    await room.close()


async def test_work_that_goes_on_keeps_the_yes_and_new_work_is_asked_again():
    room = await open_room()
    worker = Worker(asks=(("записати файл", "change"),))
    room.core.add_executor(worker)
    first = room.core.delegate("запиши")
    await room.until(lambda: room.core.state.permission is not None)
    room.says("Так")
    await room.until(lambda: worker.allowed == [True])
    worker.release.set()
    await room.until(lambda: first.status == "done")

    goes_on = room.core.delegate("а тепер друге", follows=True)
    await room.until(lambda: len(worker.allowed) == 2)
    assert goes_on.allowed is True and worker.allowed == [True, True]
    await room.until(lambda: goes_on.status == "done")

    worker.release.clear()
    room.core.delegate("щось нове")
    await room.until(lambda: room.core.state.permission is not None)
    worker.release.set()
    await room.close()


async def test_a_button_can_answer_instead_of_a_voice():
    room = await open_room()
    worker = Worker(asks=(("записати", "change"),))
    room.core.add_executor(worker)
    room.core.delegate("запиши")
    await room.until(lambda: room.core.state.permission is not None)
    assert room.core.permit(room.core.state.permission.id, True)
    await room.until(lambda: worker.allowed)
    assert worker.allowed == [True]
    await room.close()


async def test_a_promise_without_a_call_is_asked_for_again():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.model.will_say("Гляну, секунду.",
                        Says(calls=(("delegate", {"task": "глянь логи"}),)))
    room.says("Глянь логи")
    await room.until(lambda: worker.tasks)
    assert len(room.model.asked) == 2
    assert room.page.said == ["Гляну, секунду. "]
    await room.close()


async def test_a_promise_still_not_kept_is_never_handed_over_by_itself():
    """After a refusal the reply «скажи, і я зроблю» is a promise by its
    words; handing the person's «ні» to an executor was how jobs multiplied."""
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.model.will_say("Гаразд. Якщо передумаєш, скажи, і я зроблю.", "Нічого не викликаю.")
    room.says("Ні, не треба")
    await room.settled()
    assert worker.tasks == []
    await room.close()


async def test_one_piece_of_work_is_asked_about_once():
    room = await open_room()
    worker = Worker(asks=(("записати a.txt", "change"), ("запустити скрипт", "change"),
                          ("записати b.txt", "change")))
    room.core.add_executor(worker)
    task = room.core.delegate("зроби три речі")
    await room.until(lambda: room.core.state.permission is not None)
    room.says("Дозволяєм")
    await room.until(lambda: len(worker.allowed) == 3)
    assert worker.allowed == [True, True, True]
    asked = [e for e in room.core.state.journal if e.kind == "permission"]
    assert len(asked) == 1 and task.allowed is True
    await room.settled()
    assert "Добре." in room.page.said  # a yes is answered, not met with silence
    assert room.model.asked == []
    await room.close()


async def test_asking_again_for_work_in_progress_does_not_start_it_twice():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    call = ("delegate", {"task": "глянь, скільки файлів у робочій теці"})
    again = ("delegate", {"task": "скільки файлів у робочій теці, глянь ще раз"})
    room.model.will_say(Says(calls=(call,)), Says(calls=(again,)))
    room.says("Глянь, скільки файлів у теці")
    await room.until(lambda: worker.tasks)
    room.says("Ну то що там з файлами?")
    await room.settled()
    assert len(worker.tasks) == 1
    assert room.page.said[-1] == "Це вже в роботі."
    await room.close()


async def test_stop_in_any_of_the_ways_a_person_says_it():
    for phrase in ("Зупини все.", "Дока, скасуй усі справи негайно", "А ну стоп",
                   "зупини агентів", "Припини це, будь ласка"):
        room = await open_room()
        worker = Worker()
        room.core.add_executor(worker)
        room.core.delegate("довга справа")
        await room.until(lambda: worker.tasks)
        room.says(phrase)
        await room.settled()
        assert worker.stopped == 1 and room.page.said[-1] == "Зупинив.", phrase
        assert room.model.asked == [], phrase
        await room.close()


async def test_stop_about_something_else_is_not_a_command():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.core.delegate("довга справа")
    await room.until(lambda: worker.tasks)
    room.model.will_say("Це не до мене.")
    room.says("Зупини музику")
    await room.settled()
    assert worker.stopped == 0
    await room.close()


async def test_the_model_can_stop_the_work_when_asked_in_a_long_way():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.core.delegate("довга справа")
    await room.until(lambda: worker.tasks)
    room.model.will_say(Says(calls=(("stop_work", {}),)))
    room.says("Слухай, щось ті твої помічники надто довго там копаються, хай кидають")
    await room.settled()
    assert "stop_work" in room.model.offered[0]
    assert worker.stopped == 1 and room.page.said[-1] == "Зупинив."
    await room.close()


async def test_everything_that_happens_is_an_event():
    room = await open_room()
    events = room.core.state.subscribe()
    room.model.will_say("Привіт.")
    room.says("Привіт")
    await room.settled()
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    kinds = [e.kind for e in seen]
    assert kinds.index("heard") < kinds.index("said")
    assert "phase" in kinds
    assert room.core.state.phase == "idle"
    assert room.core.state.snapshot()["parts"] == {
        "inputs": ["text"], "outputs": ["text"], "executors": [],
    }
    await room.close()


async def test_what_is_always_known_is_in_every_prompt():
    room = await open_room()
    room.memory.known = "Людину звати Назар."
    room.model.will_say("Привіт, Назаре.")
    room.says("Привіт")
    await room.settled()
    assert "Людину звати Назар." in room.model.asked[0][0]["content"]
    await room.close()


async def test_the_model_knows_what_work_is_at_and_what_came_of_it():
    from heare.core.forms import Task

    room = await open_room()
    room.core.add_executor(Worker())
    room.core.state.tasks[1] = Task(1, "знайди резюме", "worker", status="done",
                                    result="Знайшов 17 PDF;\nнайновіше resume-current.pdf.")
    room.core.state.tasks[2] = Task(2, "скільки місця", "worker", step="du -sh ~")
    room.model.will_say("Сімнадцять.")
    room.says("Що ти знайшов?")
    await room.settled()

    prompt = room.model.asked[0][0]["content"]
    assert "результат: Знайшов 17 PDF; найновіше resume-current.pdf." in prompt
    assert "У РОБОТІ: скільки місця — зараз: du -sh ~" in prompt
    await room.close()


async def test_speech_not_meant_for_it_is_shown_and_not_answered():
    room = await open_room()
    events = room.core.state.subscribe()
    room.starts()
    room.line.port.overheard("text", "Чутно мене чи не чутно?")
    await room.settled()
    seen = [events.get_nowait() for _ in range(events.qsize())]
    assert [e.data["text"] for e in seen if e.kind == "overheard"] == ["Чутно мене чи не чутно?"]
    assert room.model.asked == [] and room.page.said == []
    await room.close()


async def test_room_talk_it_has_nothing_to_say_about_stays_silent_and_unkept():
    from heare.core import Message

    room = await open_room()
    room.model.will_say("МОВЧУ")
    room.core.hear(Message(text="Так, я тобі передзвоню ввечері", channel="text",
                           addressed=False))
    await room.settled()
    assert room.page.said == [] and room.memory.kept == []
    assert room.core.history == []
    assert [e.data["why"] for e in room.core.state.journal if e.kind == "overheard"] == [
        "chose silence"
    ]
    assert "не до тебе" in room.model.asked[0][0]["content"]
    await room.close()


async def test_room_talk_worth_a_word_is_answered_like_any_turn():
    from heare.core import Message

    room = await open_room()
    room.model.will_say("Так, чую тебе добре.")
    room.core.hear(Message(text="Чутно мене чи ні?", channel="text", addressed=False))
    await room.settled()
    assert room.page.said == ["Так, чую тебе добре. "]
    assert len(room.model.asked) == 1  # decided and answered in one go
    assert [r for r, _, _ in room.memory.kept] == ["user", "assistant"]
    await room.close()


async def test_room_talk_does_not_end_a_reply_in_progress():
    from heare.core import Message

    room = await open_room(slow=0.04)
    room.model.will_say("Раз два три чотири п'ять.")
    room.says("Порахуй")
    await room.until(lambda: room.page.speaking)
    room.core.hear(Message(text="Ну таке", channel="text", addressed=False))
    await room.settled()
    assert room.page.outcomes == ["done"]
    assert len(room.model.asked) == 1
    await room.close()


async def test_what_the_code_says_itself_is_part_of_the_conversation():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.core.delegate("довга справа")
    await room.until(lambda: worker.tasks)
    room.says("Зупини все")
    await room.settled()
    room.model.will_say("Нічого.")
    room.says("Що ти зараз робиш?")
    await room.settled()
    seen = [(m["role"], m["content"]) for m in room.model.asked[-1][1:]]
    assert ("user", "Зупини все") in seen and ("assistant", "Зупинив.") in seen
    assert "зараз ти нічого не робиш" in room.model.asked[-1][0]["content"]
    await room.close()


async def test_a_short_answer_that_starts_like_the_word_for_silence_is_still_said():
    from heare.core import Message

    room = await open_room()
    room.model.will_say("Мо")
    room.core.hear(Message(text="Як буде «може» скорочено?", channel="text", addressed=False))
    await room.settled()
    assert room.page.said == ["Мо "]
    await room.close()


async def test_hydrate_puts_the_journals_last_turns_before_the_new_question():
    room = await open_room()
    room.core.hydrate([
        {"role": "user", "content": "старе питання"},
        {"role": "assistant", "content": "стара відповідь"},
    ])
    room.model.will_say("нова відповідь")
    room.says("нове")
    await room.settled()

    asked = room.model.asked[0]
    old_question = {"role": "user", "content": "старе питання"}
    old_answer = {"role": "assistant", "content": "стара відповідь"}
    new_question = {"role": "user", "content": "нове"}
    assert (
        asked.index(old_question)
        < asked.index(old_answer)
        < asked.index(new_question)
    )
    await room.close()


async def test_hydrate_ignores_malformed_entries_and_keeps_the_last_turns():
    room = await open_room()
    entries = [
        {"role": "system", "content": "не розмова"},
        {"role": "user", "content": ""},
        {"role": "user"},
        {"role": "tool", "content": "результат"},
        "not a dict",
        None,
    ]
    entries += [
        {"role": "user" if index % 2 == 0 else "assistant",
         "content": f"репліка {index}"}
        for index in range(HISTORY_TURNS + 4)
    ]
    room.core.hydrate(entries)

    assert len(room.core.history) == HISTORY_TURNS
    assert room.core.history[-1] == {
        "role": "user" if (HISTORY_TURNS + 3) % 2 == 0 else "assistant",
        "content": f"репліка {HISTORY_TURNS + 3}",
    }
    assert all(m["role"] in {"user", "assistant"} for m in room.core.history)
    await room.close()


async def test_an_executor_can_ask_the_person_and_gets_their_words():
    from heare.core import Message, Task

    room = await open_room()
    task = Task(id=1, request="перевір", executor="opencode")
    asking = asyncio.create_task(room.core._question_of(task, "який файл?"))

    for _ in range(50):
        if room.core.state.question is not None:
            break
        await asyncio.sleep(0.01)
    question = room.core.state.question
    assert question is not None and question.text == "який файл?"
    assert any(e.kind == "question" for e in room.core.state.journal)

    room.core.hear(Message(text="той, що в /tmp", channel="text"))
    answer = await asyncio.wait_for(asking, 2.0)
    assert answer == "той, що в /tmp"
    assert room.core.state.question is None
    assert any(e.kind == "question_closed" for e in room.core.state.journal)
    assert ("user", "той, що в /tmp", "text") in room.memory.kept
    assert {"role": "user", "content": "той, що в /tmp"} in room.core.history
    await room.close()


async def test_the_journal_does_not_duplicate_a_turn_the_book_wrote(tmp_path):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", profile_dir=tmp_path / "memory", journal=journal
    )
    model, line, page = Script(), Line(), Page()
    core = Core(model, memory=book, names=frozenset({"дока"}), words=uk)
    core.add_input(line)
    core.add_output(page)
    model.will_say("вітаю")

    running = [
        asyncio.create_task(core.run()),
        asyncio.create_task(journal.run(core.state)),
    ]
    try:
        await asyncio.sleep(0)
        core.hear(Message(text="привіт", channel="text"))
        for _ in range(200):
            if len(journal.tail_messages()) == 2:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert len(journal.tail_messages()) == 2
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        book.close()


async def test_the_fit_can_trim_the_history_the_model_reads():
    def fit(system, turns):
        return system, turns[-1:]

    room = await open_room(fit=fit)
    room.core.hydrate([
        {"role": "user", "content": "старе"},
        {"role": "assistant", "content": "стара"},
        {"role": "user", "content": "ще"},
        {"role": "assistant", "content": "теж"},
    ])
    room.model.will_say("нова відповідь")
    room.says("нове")
    await room.settled()
    assert len(room.model.asked[0]) == 2
    assert room.model.asked[0][0]["role"] == "system"
    assert room.model.asked[0][-1] == {"role": "user", "content": "нове"}
    await room.close()


async def test_a_broken_fit_does_not_break_the_turn():
    def fit(system, turns):
        raise RuntimeError("no budget today")

    room = await open_room(fit=fit)
    room.model.will_say("все одно")
    room.says("привіт")
    await room.settled()
    assert room.page.said == ["все одно "]
    assert room.model.asked[0][-1] == {"role": "user", "content": "привіт"}
    await room.close()


class Gate:
    """Lets through what it is told to; remembers what it was shown."""

    def __init__(self, *verdicts, fails: bool = False) -> None:
        self.verdicts = list(verdicts)
        self.fails = fails
        self.shown: list[tuple[str, str, str]] = []

    async def decide(self, task, talk, what, detail):
        self.shown.append((task.request, what, detail))
        if self.fails:
            raise RuntimeError("no model")
        return self.verdicts.pop(0)


async def test_what_the_gate_finds_plainly_asked_for_is_done_without_a_question():
    gate = Gate((True, "прибирає дублікат, як просив"))
    room = await open_room(gate=gate)
    events = room.core.state.subscribe()
    worker = Worker(asks=(("trash a.pdf", "judge"),))
    room.core.add_executor(worker)
    task = room.core.delegate("прибери дублікати")
    await room.until(lambda: worker.allowed == [True])

    assert room.core.state.permission is None and room.page.said == []
    assert task.allowed is None  # the gate's yes is for that action only
    assert gate.shown == [("прибери дублікати", "trash a.pdf", "")]
    seen = [events.get_nowait() for _ in range(events.qsize())]
    judged = [e.data for e in seen if e.kind == "judged"]
    assert judged == [{"task": task.id, "what": "trash a.pdf", "allowed": True,
                       "why": "прибирає дублікат, як просив"}]
    worker.release.set()
    await room.close()


async def test_what_the_gate_brings_is_asked_with_its_reason_one_action_at_a_time():
    gate = Gate((False, "це побачать інші"), (False, "це не повернеш"))
    room = await open_room(gate=gate)
    worker = Worker(asks=(("git push", "judge"), ("rm -rf build", "judge")))
    room.core.add_executor(worker)
    room.core.delegate("опублікуй")
    await room.until(lambda: room.core.state.permission is not None)
    await room.until(lambda: room.page.said)
    assert room.page.said[0] == "worker хоче: git push. Питаю, бо це побачать інші. Дозволяєш?"
    assert room.core.state.permission.why == "це побачать інші"
    room.says("Так")
    await room.until(lambda: len(room.page.said) >= 3)  # «Добре.» and the next question
    assert room.page.said[-1] == "worker хоче: rm -rf build. Питаю, бо це не повернеш. Дозволяєш?"
    room.says("Ні")
    await room.until(lambda: len(worker.allowed) == 2)
    assert worker.allowed == [True, False]
    worker.release.set()
    await room.close()


async def test_a_gate_that_fails_brings_the_action_to_the_person():
    room = await open_room(gate=Gate(fails=True))
    worker = Worker(asks=(("trash a.pdf", "judge"),))
    room.core.add_executor(worker)
    room.core.delegate("прибери")
    await room.until(lambda: room.core.state.permission is not None)
    assert room.core.state.permission.why == "суддя не відповів"
    room.says("Так")
    await room.until(lambda: worker.allowed == [True])
    worker.release.set()
    await room.close()


async def test_a_change_the_person_must_allow_never_reaches_the_gate():
    gate = Gate((True, "так"))
    room = await open_room(gate=gate)
    worker = Worker(asks=(("записати", "change"),))
    room.core.add_executor(worker)
    room.core.delegate("запиши")
    await room.until(lambda: room.core.state.permission is not None)
    assert gate.shown == []
    room.says("Так")
    await room.until(lambda: worker.allowed == [True])
    worker.release.set()
    await room.close()
