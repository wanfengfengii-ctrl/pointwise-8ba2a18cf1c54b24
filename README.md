# 河湾环境资料湖

资料中心接收站点的日内整升采样批。值班员按明确的批序和质控修订定稿，交档员实读验卷后可释放在线位置，夜班凭固定账号核账。新批次和新修订可以用于另一账号。旧账号保留原清单、汇总和出处。

## 运行

Python 3.11，使用标准库。控制台物化后运行完整回归。

```text
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t .
python3 -B demo.py
```

演示在临时库中录入两站 [0,7,null] 和 [11,25]。0 至 20 升规则下，分站计 7、11 升，全日计 18 升，缺测和范围外各一条。随后发布固定账、归档第二卷、退温并重开，核对该账号只报告第二卷缺失且无部分明细。业务场景行在实际断言通过后输出。

## Python 接口

`lake.open_lake(path)` 打开并初始化本地 SQLite 库，调用方负责关闭连接。`lake.initialize(db)` 可重复调用并保留既有数据。业务操作各自管理读写事务，传入的连接须处于事务外。批号、站点、定稿号、规则号和冷目标使用非空文本，日期使用 `YYYY-MM-DD`。

| 模块 | 调用与结果 |
| --- | --- |
| `lake.intake.batches` | `receive_batch(db, station, day, number, values)` 返回含 `identity`、`row_count` 和 `code` 的首执。原行从 1 编号。同身份按有序值比较，同件回原首执，异件拒绝。 |
| `lake.quality.rules` | `register_ruleset(db, revision, lower, upper)` 登记非负整数闭区间。修订号不可覆写，同号同口径可重投。 |
| `lake.quality.day_report` | `preview_day(db, day, batches, revision)` 接收首执中的 `BatchIdentity`，按首次选批顺序返回逐行判定和分站、全日汇总。 |
| `lake.publication.daybooks` | `publish_day(db, day, batches, revision, number)` 返回固定清单、汇总、收据。空选批可定稿。重投会比较日期、规范化选批顺序及修订。 |
| `lake.custody.cold_copy` | `archive_volume(db, volume_id, target)` 将已发布卷存到冷目标并从该目标实际复读，返回 `InspectionRecord`。冷正文位置由目标和卷身份共同寻址。 |
| `lake.custody.retirement` | `retire_volume(db, volume_id, record)` 校对原验卷记录和当前实物，移除在线正文，返回固定卷身份。凭同一仍有效记录重试可重领结果。 |
| `lake.reading.daybook` | `read_day(db, number)` 返回 `ReadResult` 或 `ReadUnavailable`。历史明细按清单和批内原序交付。缺卷标识按清单首次出现排列，`details` 为空。 |

来件允许 Python 整数和 `None`。零有效，布尔值、浮点值和数字文本非法。整数界限和数值在 SQLite 与卷编码中使用十六进制文本，公开接口按整数计算；缺测使用独立空值。空批可被选入定稿，只有非空批生成卷和分站行。冻结正文包括原值、判定、站点、日期、批号和源位置，独立于账的账号。复用卷时校验实际完整正文并沿用现有冷热位置。

输入验证和冲突使用 `ValueError` 或 `ConflictError`，未知批次、规则、账或卷使用 `KeyError`。冷实物失配和存储完整性失败使用 `DataIntegrityError`。数据库写入错误会回滚并传播。核读把缺失或校验不过的在线正文视为缺卷。完整核账结果使用所请求账号的冻结汇总。

`tests/cold_fixture.py` 提供离线设施测试。实体槽持久保存固定容量和实际字节，冷库夹具从真实冷目标取得字节并供测试选择分片及终结通知的送达顺序。夹具中的冷库可在湖库连接关闭期间继续保有已发通知。
