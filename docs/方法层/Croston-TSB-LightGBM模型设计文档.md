# Croston-TSB-LightGBM 间歇性需求预测模型设计文档

> 版本：v1.1 | 日期：2026-07-06
> 目标：将 Croston-TSB 需求解耦框架与 LightGBM 梯度提升树结合，在冀北电力物资月度需求数据上实现间歇性需求预测
> 状态：已完成代码实现与实验评估

---

## 一、可行性分析

### 1.1 当前 TwoStage 的失败模式

两阶段预测模型说明.md 中记录了现有 CatBoost TwoStage 在五种物资上的表现：

| 物资 | 非零月 | TwoStage R² | CatBoost R² | 失败模式 |
|------|:--:|:--:|:--:|------|
| 控制电缆 | 15/74 (20%) | -0.15 | -0.15 | 两类零值混淆 |
| 电缆保护管 | 17/74 (23%) | -0.42 | +0.08 | VMD 噪声放大 |
| 通信单元 | 13/74 (18%) | +0.17 | -0.02 | 仅 VMD-LSTM-CatBoost 勉强 |
| 电缆接线端子 | 13/74 (18%) | -0.11 | -0.24 | 全部负 R² |
| 蝶式绝缘子 | 12/74 (16%) | +0.49 | -0.13 | 意外好,但孤立 |

**核心失败原因：P × Q 乘性误差放大。** 对于月均需求 < 100 的物资，Stage 1 分类器（CatBoostClassifier）在"会有需求吗"上的一个微小概率误判（如 P 实际=0 被预测为 P=0.25），乘以 Stage 2 回归器预测的需求量，产生假阳性预测值，在稀疏的测试集中占主导。

### 1.2 Croston-TSB 的解决原理

Croston 方法（1972）将间歇性需求拆解为两个独立的过程：

```
Y_t = (I_t = 1) × Z_t

其中:
  I_t ~ Bernoulli(p_t)    ← "这个月有需求吗" — 由需求间隔决定
  Z_t ~ F(μ_t)           ← "如果有，量是多少" — 需求量分布

CROSTON 框架的独特优势:
  不预测 74 个月的月度值 — 预测 interval 和 size 两个序列
  interval: [3, 5, 2, 1, 7, ...]  — 每次需求间隔的月数
  size:     [86887, 49750, 240000, ...] — 每次非零需求量
```

TSB (Teunter-Syntetos-Babai, 2011) 的改进：加入需求概率衰减——如果距离上次需求已超过历史平均间隔的 2 倍，需求发生概率指数衰减。这对处理冀北电网中"某规格某年突然不再采购"的场景至关重要。

**将 Croston 的指数平滑参数估计替换为 LightGBM 的梯度提升，在已发表的电力物资预测文献中是空白。** 50 年来 Croston 族方法全部使用指数平滑，这一替换本身就是方法论创新。

### 1.3 文献空白确认

| 已有方法 | 代表论文 | 我们的改进 |
|---------|---------|-----------|
| Croston + SES | Croston (1972) | SES → LightGBM |
| Croston + SBA | Syntetos-Boylan (2005) | 仅偏差修正, 无 ML |
| TSB + SES | Teunter et al. (2011) | SES → LightGBM |
| TwoStage CatBoost | 本项目的 TwoStage | 完全不同的解耦逻辑 |

**没有人将 Croston-TSB 的指数平滑替换为梯度提升树。** 审稿人会问"为什么不直接用 Croston"——答案是因为指数平滑无法捕获电力物资需求的非线性模式（季度集中采购 + 年度增长趋势 + 原材料价格冲击）。

---

## 二、设计文档

### 2.1 架构总览

```
ECP MATERIAL DEMAND DATA (74 months, 12-17 non-zero)
│
├── Step 1: 提取 Croston 序列
│   ├── interval_sequence: 每次非零需求之间的间隔(月数)
│   │   例: [8, 4, 3, 5, 1, 3, 3, 7, 8, 3, 6, 2]  (控制电缆: 15个事件 → 14个间隔)
│   └── size_sequence: 每次非零需求的数量
│       例: [86887, 81640, 120800, ... ]
│
├── Step 2: TSB 概率衰减
│   ├── 计算需求发生概率 p_t
│   │   p_t = 1 / interval_hat  (基本概率)
│   │   if months_since_last > 2 × mean(interval):
│   │       p_t *= 0.5 ** (months_since_last / mean(interval))
│   └── 输出: 未来 12 个月的需求发生概率
│
├── Step 3: LightGBM 模型
│   ├── LightGBM_interval: 预测下一个间隔 (回归)
│   │   特征: month_sin, month_cos, year_index, prev_interval, avg_interval,
│   │         copper_price, pmi, grid_invest
│   └── LightGBM_size: 预测下一个需求量 (回归)
│       特征: month_sin, month_cos, year_index, prev_size, avg_size,
│             copper_price, pmi, grid_invest
│
├── Step 4: 合成月度预测
│   ├── 对于第 h 个预测月:
│   │   prob_h = min(1.0 / interval_pred, 1.0)  ← prob这个月有需求
│   │   quant_h = max(size_pred, 0)              ← 如果有,需要多少
│   │   forecast_h = prob_h × quant_h
│   └── 输出: 12 个月需求预测
│
└── Step 5: 评估
    ├── MAE, RMSE, R² (与 CatBoost TwoStage 对比)
    └── 需求间隔预测准确率 (新增指标: 论文创新点)
```

### 2.2 输入特征设计

#### Interval 模型特征 (8维)

| 特征 | 类型 | 说明 |
|------|------|------|
| month_sin | float | sin(2π×month/12), 季节编码 |
| month_cos | float | cos(2π×month/12), 季节编码 |
| year_index | int | 年份索引 (0=2020) |
| prev_interval | float | 上一个需求间隔(月数) |
| avg_interval_3 | float | 最近3次间隔的移动平均 |
| avg_interval_all | float | 全部历史间隔的均值 |
| days_since_last | int | 距上次需求已过去几个月 |
| copper_price_lag1 | float | 上月铜期货价格(元/吨) |

#### Size 模型特征 (8维)

| 特征 | 类型 | 说明 |
|------|------|------|
| month_sin | float | 季节编码 |
| month_cos | float | 季节编码 |
| year_index | int | 年份索引 |
| prev_size | float | 上一次需求量 |
| avg_size_3 | float | 最近3次需求量的移动平均 |
| avg_size_all | float | 全部历史非零需求的均值 |
| grid_invest | float | 冀北电网投资量(亿元), 同月 |
| copper_price | float | 铜期货月均价(元/吨) |

### 2.3 LightGBM 超参数

```python
# Interval model (小样本 → 浅树, 强正则化)
lgb_interval_params = dict(
    n_estimators=50,        # 极少树, 15个样本不可能支持更多
    max_depth=2,            # 极浅树防止过拟合
    min_child_samples=3,    # 叶子最少样本
    learning_rate=0.05,
    reg_alpha=1.0,          # L1 正则化
    reg_lambda=2.0,         # L2 正则化
    verbosity=-1,
)

# Size model (小样本 → 同上)
lgb_size_params = dict(
    n_estimators=50,
    max_depth=2,
    min_child_samples=3,
    learning_rate=0.05,
    reg_alpha=1.0,
    reg_lambda=2.0,
    verbosity=-1,
)
```

### 2.4 回退机制

当数据不满足 Croston 框架的前提时：

```python
if len(intervals) < 3:  # <3个间隔 → 无法估计interval模式
    return naive_seasonal_forecast()  # 回退到"同期去年"朴素预测

if len(sizes) < 3:  # <3个非零量 → 无法估计size模式
    return naive_mean_forecast()  # 回退到历史均值
```

---

## 三、预期与风险

### 3.1 预期提升

| 物资 | CatBoost TwoStage R² | 预期 Croston-LGB R² | 提升来源 |
|------|:--:|:--:|------|
| 控制电缆 | -0.15 | +0.10 ~ +0.30 | interval/size 解耦消除零值混淆 |
| 电缆保护管 | -0.42 | -0.10 ~ +0.15 | 不再依赖 VMD |
| 通信单元 | +0.17 | +0.15 ~ +0.35 | 间隔模型天然编码零值 |
| 电缆接线端子 | -0.11 | -0.05 ~ +0.10 | LightGBM 浅树抗过拟合 |
| 蝶式绝缘子 | +0.49 | +0.45 ~ +0.60 | 保持或略改善 |

### 3.2 已知风险

- 样本量极端小 (14 个间隔、15 个 size) → 即使 n_estimators=50 仍有风险
- interval 和 size 的独立性假设不完美（大量采购后间隔可能更长）
- TSB 的概率衰减速率依赖于超参数设定

---

## 四、实验结果

### 4.1 实验设置

- 数据：国网冀北电力 ECP 真实采购数据 (2020-05 ~ 2026-06, 74个月)
- 5 种物资：控制电缆(15非零月)、电缆保护管(17)、通信单元(13)、电缆接线端子(13)、蝶式绝缘子(12)
- Train: 2020-05 ~ 2025-06 (62个月) | Test: 2025-07 ~ 2026-06 (12个月)
- 对比方法：Naive-Seasonal(重复去年同月)、CatBoost 单阶段、CatBoost TwoStage

### 4.2 R² 对比表

| 物资 | Naive-Seasonal | Croston-TSB-LGB | LGB-Direct | CatBoost | TwoStage |
|------|:--:|:--:|:--:|:--:|:--:|
| 控制电缆 | **0.6260** | -0.1206 | 0.1972 | -0.1509 | 0.0968 |
| 电缆保护管 | -2.6298 | -0.0809 | -0.0591 | **0.0775** | 0.1580 |
| 通信单元 | -0.1391 | -0.0872 | -0.0154 | -0.0151 | **0.1702** |
| 电缆接线端子 | -0.3368 | -0.0807 | -0.0131 | -0.2410 | **-0.0260** |
| 蝶式绝缘子 | 0.3261 | -0.1466 | -0.0441 | -0.1281 | **0.4936** |

### 4.3 逐物资分析

#### 控制电缆 — Naive-Seasonal 碾压所有模型 (R²=0.63)

简单重复"去年同月"的预测 R²=0.63，而 Croston-TSB-LGB=-0.12。Croston 的预测是固定的均值衰减（每月递减约 900），完全无法捕捉"某月突然变大量"的现实。

测试集只有 2 个非零月（2025年10月=10010, 2026年3月=86887）。Croston 预测的 interval=4.7 个月无法解释 test 期实际出现的 5 个月和 5 个月间隔——这些偏离了历史均值 2 个标准差。

#### 电缆保护管 — 全部模型不如 CatBoost

CatBoost 基线 R²=0.08。Croston-TSB-LGB=-0.08。17 个非零月但需求量极度分散（CV=1.09, 620~483,765）。Croston 用平均 size=134,104 去预测，在 10/12 的零值月产生巨大的假阳性。

#### 通信单元 — 全部负值

测试集仅 2 个非零月（2,289,653 和 531,413）。两个极端值都是正常采购量的 2~3 倍。任何基于历史均值的预测器都严重低估，同时对另外 10 个零值月产生假阳性。

#### 电缆接线端子 — LGB-Direct 最好 (R²=-0.0131)

虽然仍是负值，但相对 TwoStage (R²=-0.03)、Croston (-0.08)、CatBoost (-0.24) 有最大改善。这个物资波动相对均匀（CV=0.82），LightGBM 的 leaf-wise 生长比 CatBoost 的对称树更灵活。

#### 蝶式绝缘子 — TwoStage 仍然最好 (R²=0.49)

在所有方法中唯一保持显著正值的模型。但这个 R²=0.49 是 CatBoost TwoStage 的（之前实验），不是 Croston-TSB-LGB 的（-0.15）。

### 4.4 失败模式归纳

#### 失败一：均值回归 — Croston 只能用固定预测

```
控制电缆测试值:   [0, 0, 0, 10010, 0, 0, 0, 0, 86887, 0, 0, 0]
Croston预测:      [9099, 9099, 9099, 9099, 9099, 1913, 1608, 1353, ...]
```

Croston 从 14 个样本中学到的 interval=4.7、size=47,188，输出一条单调衰减曲线。对"长期沉默→突然跳变"的模式无能为力——14 个样本不足以学到这一规律。

#### 失败二：TSB 衰减机制对间歇性（非永久消失）模式适得其反

TSB 假设需求可能"永远停止"——当间隔 > 2×历史平均时概率指数衰减。但 ECP 物资的间歇是常态模式，不是停止信号。10 个月的无采购后，下一个月的概率不应趋近于 0——历史中确实存在间隔 10 个月的采购事件。

#### 失败三：12-17 个事件 < 任何 ML 模型的统计学下限

LightGBM n_estimators=50 在 14 个样本上，每个 split 只有 ~7 个样本。信息增益无意义——任何分割都是噪声驱动。模型退化为某种复杂的均值加权。

### 4.5 改进方向

#### 短期（1-2天）

1. **Naive-Seasonal as anchor + Croston size modifier**
   - 用"去年同期"预测"哪月会有需求"（已经在 5 种物资中 2 种的 R²>0.3）
   - 用 Croston 的 size 预测修正"需求量级"
   - 伪代码: `forecast[h] = seasonal_pattern[h] * (croston_size / historical_mean_size)`

2. **多物资联合间隔模型**
   - 5 种物资共享同一个 interval 预测模型
   - 特征中加入 `material_id` 编码
   - 样本量从 14 → 70 (5品×14)
   - 这是"面板数据 LightGBM"的最简单形式

3. **批次同步信号**
   - `project_count`（该月招标项目数）作为外部冲击信号的强度
   - 有招标的月份 → 所有物资的需求概率同时上调
   - 这个思路不需要增加训练样本

#### 中长期（3-5天）

4. **事件预测范式转换**
   - 不预测月度需求 → 预测"下个批次在哪个月、量大不大"
   - 将序列从 (74, 1) 变成 (批次序列, 2) = (月份, 需求量)
   - 这是一个离散事件时间预测 + 标记值回归的组合

5. **放弃时序方法，转向聚类+类比**
   - 对每种物资计算"需求画像"（间隔均值/CV/季节模式）
   - 测试期的物资如果和训练期的某种物资画像相似 → 用该物资的模式预测
   - 本质是"基于相似度的迁移学习"，而不是"基于序列的外推"

#### 方法论反思

**问题不是模型选择，而是信息量不足。** 对于 15 个非零值 / 74 个月长的序列，Croston、LightGBM、CatBoost 面对的是同一个统计下限：14 个样本。解决方向只能是"增加有效信息"——跨物资共享、批次信号、事件预测——而非在模型间切换。
