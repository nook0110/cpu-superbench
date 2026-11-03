# SuperBench на CPU

Проект НИС, задание [P06. SuperBench: упреждающая проверка вычислительной инфраструктуры](https://distsys.ru/osukhoroslov/nis2/src/branch/main/project-tasks/p06-superbench.md).

Участники:

- Блохтин Никита
- Верютина Алина
- Лазо Арсений

Мы делаем маленькую версию SuperBench для обычного компьютера без видеокарты. В ней 8 коротких тестов для процессора, памяти, диска и сети, способы искусственно замедлить каждую из этих частей и два алгоритма из статьи. Validator решает, замедлился ли компьютер, а Selector выбирает, какие тесты запускать.

## Запуск

Нужен Linux и Python 3.10 или новее.

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
python3 -m minisb check
python3 -m minisb check --faults all --output results/checkpoint-1/checks.jsonl
```

Команда `check` сначала запускает тесты как есть, потом с каждым замедлением из `--faults` и показывает, во сколько раз изменился результат. `all` значит по одному замедлению каждого из 9 видов, можно перечислить и свои, например `--faults cpu_quota:0.5,net_delay:1ms`.

## Навигация

- [Описание проекта](nis/project.md)
- [План эксперимента](nis/experiment-plan.md)
- [Контрольная точка 1](nis/checkpoints/01.md)
- Код: [minisb/](minisb/). Тесты лежат в [probes.py](minisb/probes.py), замедления в [faults.py](minisb/faults.py), алгоритмы из статьи в [validator.py](minisb/validator.py) и [selector.py](minisb/selector.py)
- Модульные тесты: [tests/](tests/)
- Результаты: [results/](results/)
