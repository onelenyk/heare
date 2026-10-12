"""The core, attached to stand-ins: what a person would notice.

No sound and no network. Every scenario is the core talking to the five
forms, so whatever passes here holds for any channel, model or executor
that has the form.
"""

from __future__ import annotations

from heare.core import FAILURE_LINE, RESULT_PREFIX
from tests.heare.fakes import Page, Says, Worker, open_room


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


async def test_stop_with_nothing_running_just_agrees():
    room = await open_room()
    room.says("стоп")
    await room.settled()
    assert room.page.said == ["Добре."]
    assert room.model.asked == []
    await room.close()


async def test_recall_is_said_as_found():
    room = await open_room()
    room.model.will_say(Says(calls=(("recall", {"query": "відпустка"}),)))
    room.says("Що я казав про відпустку?")
    await room.settled()
    assert room.page.said == ["Ти казав про це вчора."]
    await room.close()


async def test_a_verb_is_offered_only_when_it_can_work():
    room = await open_room(memory=False)
    room.model.will_say("Привіт.")
    room.says("Привіт")
    await room.settled()
    assert room.model.offered == [[]]
    room.core.add_executor(Worker())
    room.model.will_say("Привіт ще раз.")
    room.says("Привіт")
    await room.settled()
    assert room.model.offered[-1] == ["delegate"]
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
    await room.close()


async def test_a_no_refuses_and_silence_refuses():
    room = await open_room(permission_timeout=0.1)
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
    room = await open_room(permission_timeout=0.3)
    worker = Worker(asks=(("надіслати листа", "change"),))
    room.core.add_executor(worker)
    room.core.delegate("надішли")
    await room.until(lambda: room.core.state.permission is not None)
    from heare.core import Message
    room.core.hear(Message(text="так", channel="worker", person=False))
    await room.until(lambda: worker.allowed, timeout=2.0)
    assert worker.allowed == [False]
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
    room.model.will_say("Так, чую.", "Так, чую тебе добре.")
    room.core.hear(Message(text="Чутно мене чи ні?", channel="text", addressed=False))
    await room.settled()
    assert room.page.said == ["Так, чую тебе добре. "]
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
