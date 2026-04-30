# 科研 Agent 核心实现指南 —— 从零构建自主研究智能体

## 一、Agent 的本质

Agent = LLM + 记忆 + 工具调用 + 自主循环

```
┌─────────────────────────────────────────┐
│           科研 Agent 核心架构              │
├─────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
│  │  感知层  │  │  推理层  │  │  执行层  │ │
│  │ Perceive│  │ Reason  │  │ Execute │ │
│  └────┬────┘  └────┬────┘  └────┬────┘ │
│       │            │            │      │
│       └────────────┼────────────┘      │
│                    │                   │
│              ┌─────┴─────┐             │
│              │   记忆层   │             │
│              │  Memory   │             │
│              └───────────┘             │
└─────────────────────────────────────────┘
```

## 二、核心循环（The Agent Loop）

```python
class ResearchAgent:
    def __init__(self, llm, memory, tools):
        self.llm = llm          # 大语言模型
        self.memory = memory    # 记忆系统
        self.tools = tools      # 工具集合
    
    def run(self, research_goal):
        """主循环：感知 → 推理 → 执行 → 学习"""
        
        # 1. 初始化研究任务
        self.memory.set_goal(research_goal)
        
        while not self.memory.is_complete():
            # 2. 感知：获取当前状态
            context = self.perceive()
            
            # 3. 推理：决定下一步行动
            action = self.reason(context)
            
            # 4. 执行：调用工具或生成内容
            result = self.execute(action)
            
            # 5. 学习：更新记忆
            self.learn(result)
            
            # 6. 检查是否完成
            if self.should_stop():
                break
        
        return self.memory.get_results()
    
    def perceive(self):
        """感知：从记忆和环境获取信息"""
        return {
            'goal': self.memory.get_goal(),
            'history': self.memory.get_history(),
            'current_state': self.memory.get_current_state(),
            'available_tools': list(self.tools.keys())
        }
    
    def reason(self, context):
        """推理：LLM 决定下一步行动"""
        prompt = self.build_reasoning_prompt(context)
        response = self.llm.generate(prompt)
        return self.parse_action(response)
    
    def execute(self, action):
        """执行：调用工具或生成内容"""
        if action['type'] == 'tool_call':
            tool = self.tools[action['tool_name']]
            return tool(**action['params'])
        elif action['type'] == 'generate':
            return self.llm.generate(action['prompt'])
        elif action['type'] == 'terminate':
            return {'status': 'completed', 'result': action['result']}
    
    def learn(self, result):
        """学习：更新记忆"""
        self.memory.add_experience(result)
        self.memory.update_state(result)
    
    def should_stop(self):
        """判断是否终止"""
        return self.memory.is_goal_achieved() or self.memory.is_max_steps_reached()
```

## 三、记忆系统（Memory）

### 3.1 记忆类型

```python
class Memory:
    """科研 Agent 记忆系统"""
    
    def __init__(self):
        self.short_term = []      # 短期记忆：当前对话上下文
        self.long_term = {}       # 长期记忆：知识库、经验
        self.episodic = []        # 情景记忆：历史实验记录
        self.semantic = {}        # 语义记忆：领域知识
    
    def add_experience(self, experience):
        """添加经验到情景记忆"""
        self.episodic.append({
            'timestamp': time.time(),
            'action': experience['action'],
            'result': experience['result'],
            'reflection': experience.get('reflection', '')
        })
    
    def get_relevant_experiences(self, query, k=5):
        """检索相关经验（RAG）"""
        # 使用向量检索或关键词匹配
        return self._retrieve_similar(query, self.episodic, k)
    
    def update_knowledge(self, key, value):
        """更新语义记忆"""
        self.semantic[key] = {
            'value': value,
            'updated_at': time.time(),
            'source': value.get('source', 'unknown')
        }
```

### 3.2 记忆结构示例

```json
{
  "goal": "发现新型超导材料",
  "hypothesis": {
    "current": "铜氧化物掺杂稀土元素可能提高临界温度",
    "status": "testing",
    "confidence": 0.7
  },
  "experiments": [
    {
      "id": "exp_001",
      "type": "simulation",
      "params": {"dopant": "La", "concentration": 0.15},
      "result": {"tc": 95, "success": true},
      "lesson": "La掺杂有效，但浓度需优化"
    }
  ],
  "knowledge_graph": {
    "nodes": ["超导理论", "铜氧化物", "稀土元素"],
    "edges": [
      {"from": "稀土元素", "to": "铜氧化物", "relation": "doping"}
    ]
  }
}
```

## 四、推理系统（Reasoning）

### 4.1 推理模式

```python
class ReasoningEngine:
    """推理引擎：决定 Agent 如何思考"""
    
    def __init__(self, llm):
        self.llm = llm
    
    def chain_of_thought(self, problem):
        """链式思考：逐步推理"""
        prompt = f"""
        问题：{problem}
        
        请逐步思考：
        1. 首先，理解问题的核心是什么
        2. 然后，分析已知条件和约束
        3. 接着，提出可能的解决方案
        4. 评估每个方案的优缺点
        5. 选择最佳方案并给出理由
        
        思考过程：
        """
        return self.llm.generate(prompt)
    
    def reflection(self, past_actions, current_state):
        """反思：评估过去行动"""
        prompt = f"""
        历史行动：
        {past_actions}
        
        当前状态：
        {current_state}
        
        请反思：
        1. 哪些行动是成功的？为什么？
        2. 哪些行动失败了？原因是什么？
        3. 从失败中学到了什么？
        4. 下一步应该如何调整策略？
        
        反思：
        """
        return self.llm.generate(prompt)
    
    def hypothesis_generation(self, observations):
        """假设生成：基于观察提出假设"""
        prompt = f"""
        观察数据：
        {observations}
        
        基于以上观察，请生成3个可验证的假设：
        1. 假设1：[具体描述]
           - 可验证性：如何验证
           - 预期结果：如果正确会得到什么结果
        
        2. 假设2：...
        3. 假设3：...
        """
        return self.llm.generate(prompt)
    
    def experiment_design(self, hypothesis, available_tools):
        """实验设计：规划验证实验"""
        prompt = f"""
        假设：{hypothesis}
        
        可用工具：
        {available_tools}
        
        请设计实验：
        1. 实验目的：验证什么
        2. 实验步骤：具体怎么做
        3. 需要工具：调用哪些工具
        4. 预期结果：成功/失败的判断标准
        5. 备选方案：如果失败怎么办
        """
        return self.llm.generate(prompt)
```

### 4.2 推理链自我进化（类似 DeepSeek-R1）

```python
class SelfEvolution:
    """推理链自我进化"""
    
    def improve_reasoning(self, reasoning_chain, result):
        """根据结果改进推理链"""
        if result['success']:
            # 提取成功模式
            pattern = self.extract_pattern(reasoning_chain)
            self.memory.add_success_pattern(pattern)
        else:
            # 分析失败原因
            failure_analysis = self.analyze_failure(reasoning_chain, result)
            self.memory.add_failure_lesson(failure_analysis)
    
    def extract_pattern(self, reasoning_chain):
        """提取成功推理模式"""
        # 识别关键推理步骤
        key_steps = []
        for step in reasoning_chain:
            if step['type'] == 'critical_insight':
                key_steps.append(step)
        return {'pattern': key_steps, 'context': reasoning_chain[0]['context']}
```

## 五、工具系统（Tools）

### 5.1 工具定义

```python
class Tool:
    """工具基类"""
    
    def __init__(self, name, description, params_schema):
        self.name = name
        self.description = description
        self.params_schema = params_schema
    
    def execute(self, **params):
        raise NotImplementedError
    
    def get_schema(self):
        return {
            'name': self.name,
            'description': self.description,
            'parameters': self.params_schema
        }

class SimulationTool(Tool):
    """仿真工具示例"""
    
    def __init__(self):
        super().__init__(
            name='run_simulation',
            description='运行物理仿真',
            params_schema={
                'type': 'object',
                'properties': {
                    'model': {'type': 'string', 'description': '仿真模型'},
                    'params': {'type': 'object', 'description': '仿真参数'}
                },
                'required': ['model', 'params']
            }
        )
    
    def execute(self, model, params):
        # 调用仿真引擎
        result = simulation_engine.run(model, params)
        return {
            'status': 'success',
            'data': result,
            'metrics': self.extract_metrics(result)
        }

class AnalysisTool(Tool):
    """数据分析工具"""
    
    def __init__(self):
        super().__init__(
            name='analyze_data',
            description='分析实验数据',
            params_schema={
                'type': 'object',
                'properties': {
                    'data': {'type': 'array', 'description': '实验数据'},
                    'method': {'type': 'string', 'description': '分析方法'}
                }
            }
        )
    
    def execute(self, data, method='statistical'):
        # 执行分析
        if method == 'statistical':
            return self.statistical_analysis(data)
        elif method == 'ml':
            return self.ml_analysis(data)
```

### 5.2 工具注册与发现

```python
class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self.tools = {}
    
    def register(self, tool):
        self.tools[tool.name] = tool
    
    def get_available_tools(self):
        return [tool.get_schema() for tool in self.tools.values()]
    
    def execute(self, tool_name, params):
        if tool_name not in self.tools:
            raise ValueError(f"Tool {tool_name} not found")
        return self.tools[tool_name].execute(**params)
```

## 六、完整 Agent 示例

```python
class ScientificResearchAgent:
    """完整科研 Agent 实现"""
    
    def __init__(self, config):
        # 初始化组件
        self.llm = LLMClient(config['model'])
        self.memory = Memory()
        self.tools = ToolRegistry()
        self.reasoning = ReasoningEngine(self.llm)
        
        # 注册工具
        self.setup_tools()
    
    def setup_tools(self):
        """注册科研工具"""
        self.tools.register(SimulationTool())
        self.tools.register(AnalysisTool())
        self.tools.register(LiteratureSearchTool())
        self.tools.register(CodeGenerationTool())
    
    def research(self, topic):
        """执行研究任务"""
        print(f"🔬 开始研究：{topic}")
        
        # Phase 1: 文献调研
        literature = self.conduct_literature_review(topic)
        
        # Phase 2: 假设生成
        hypotheses = self.generate_hypotheses(literature)
        
        # Phase 3: 实验验证
        for hypothesis in hypotheses:
            result = self.test_hypothesis(hypothesis)
            if result['success']:
                break
        
        # Phase 4: 结果总结
        paper = self.write_paper(result)
        
        return paper
    
    def conduct_literature_review(self, topic):
        """文献调研"""
        # 搜索相关文献
        papers = self.tools.execute('search_literature', {'query': topic})
        
        # 分析文献
        summary = self.llm.generate(f"总结以下文献：{papers}")
        
        self.memory.update_knowledge('literature', summary)
        return summary
    
    def generate_hypotheses(self, literature):
        """生成假设"""
        return self.reasoning.hypothesis_generation(literature)
    
    def test_hypothesis(self, hypothesis):
        """验证假设"""
        # 设计实验
        experiment = self.reasoning.experiment_design(
            hypothesis, 
            self.tools.get_available_tools()
        )
        
        # 执行实验
        result = self.execute_experiment(experiment)
        
        # 分析结果
        analysis = self.tools.execute('analyze_data', {'data': result})
        
        return {
            'success': analysis['p_value'] < 0.05,
            'data': result,
            'analysis': analysis
        }
    
    def execute_experiment(self, experiment):
        """执行实验"""
        results = []
        for step in experiment['steps']:
            result = self.tools.execute(step['tool'], step['params'])
            results.append(result)
            self.memory.add_experience({'step': step, 'result': result})
        return results
    
    def write_paper(self, result):
        """撰写论文"""
        template = self.memory.get('paper_template')
        content = self.llm.generate(f"""
        基于以下研究结果撰写论文：
        {result}
        
        使用模板：{template}
        """)
        return content
```

## 七、关键设计模式

### 7.1 ReAct 模式（Reasoning + Acting）

```python
class ReActAgent:
    """ReAct 模式：推理和行动交替"""
    
    def react(self, observation):
        """ReAct 循环"""
        thought = self.reason(observation)
        action = self.decide_action(thought)
        result = self.execute(action)
        
        return {
            'thought': thought,
            'action': action,
            'observation': result
        }
```

### 7.2 Plan-and-Solve 模式

```python
class PlanSolveAgent:
    """Plan-and-Solve：先规划再执行"""
    
    def plan(self, goal):
        """制定计划"""
        plan = self.llm.generate(f"为达成目标制定步骤计划：{goal}")
        return self.parse_plan(plan)
    
    def solve(self, plan):
        """执行计划"""
        results = []
        for step in plan:
            result = self.execute_step(step)
            results.append(result)
            
            # 动态调整计划
            if not result['success']:
                plan = self.replan(plan, step, result)
        
        return results
```

### 7.3 Multi-Agent 协作模式

```python
class MultiAgentResearch:
    """多 Agent 科研协作"""
    
    def __init__(self):
        self.agents = {
            'hypothesis': HypothesisAgent(),
            'experiment': ExperimentAgent(),
            'analysis': AnalysisAgent(),
            'writing': WritingAgent()
        }
    
    def collaborate(self, topic):
        """协作流程"""
        # Agent 1: 生成假设
        hypothesis = self.agents['hypothesis'].generate(topic)
        
        # Agent 2: 设计实验
        experiment = self.agents['experiment'].design(hypothesis)
        
        # Agent 3: 分析结果
        analysis = self.agents['analysis'].analyze(experiment)
        
        # Agent 4: 撰写论文
        paper = self.agents['writing'].write(analysis)
        
        return paper
```

## 八、借鉴的论文和项目

### 8.1 核心论文

| 论文 | 核心贡献 | 可借鉴点 |
|------|---------|---------|
| **AI Scientist (Sakana AI, 2024)** | 全自动科研闭环 | 代码生成、实验执行、论文撰写流程 |
| **ReAct (Yao et al., 2022)** | 推理+行动交替 | 思考-行动-观察循环 |
| **Reflexion (Shinn et al., 2023)** | 自我反思改进 | 失败经验学习、策略调整 |
| **AutoGPT / BabyAGI** | 通用 Agent 框架 | 任务分解、优先级管理 |
| **Multi-Agent Debate** | 多 Agent 协作 | 观点碰撞、共识达成 |
| **ChemCrow** | 化学领域 Agent | 领域工具调用、安全控制 |
| **GPT-Researcher** | 文献调研 Agent | 搜索、总结、引用管理 |

### 8.2 开源项目

| 项目 | 链接 | 特点 |
|------|------|------|
| **AutoGPT** | github.com/Significant-Gravitas/AutoGPT | 通用 Agent，插件丰富 |
| **MetaGPT** | github.com/geekan/MetaGPT | 多 Agent 协作，角色分工 |
| **ChatDev** | github.com/OpenBMB/ChatDev | 软件开发多 Agent |
| **ResearchGPT** | 多个实现 | 文献调研专用 |
| **AI-Scientist** | Sakana AI | 全自动科研（未开源） |

## 九、实现建议

### 9.1 最小可行产品（MVP）

```python
# 最简单的科研 Agent
class MinimalResearchAgent:
    def __init__(self, llm_api_key):
        self.llm = OpenAI(api_key=llm_api_key)
        self.memory = []
    
    def research(self, question):
        # 1. 思考
        thought = self.llm.generate(f"如何研究：{question}？")
        
        # 2. 行动（搜索文献）
        papers = self.search_papers(thought)
        
        # 3. 总结
        summary = self.llm.generate(f"总结文献：{papers}")
        
        # 4. 提出假设
        hypothesis = self.llm.generate(f"基于总结提出假设：{summary}")
        
        return {
            'thought': thought,
            'papers': papers,
            'summary': summary,
            'hypothesis': hypothesis
        }
```

### 9.2 渐进增强路线

1. **Phase 1**: 简单 ReAct 循环（思考→行动→观察）
2. **Phase 2**: 添加记忆系统（短期+长期）
3. **Phase 3**: 工具调用（搜索、分析、仿真）
4. **Phase 4**: 自我反思（Reflexion）
5. **Phase 5**: 多 Agent 协作
6. **Phase 6**: 闭环实验（仿真+真实）

## 十、总结

科研 Agent 的核心是：

1. **循环**：感知 → 推理 → 执行 → 学习
2. **记忆**：保存经验、知识、失败教训
3. **推理**：链式思考、反思、假设生成
4. **工具**：调用外部能力扩展 Agent
5. **进化**：从成功/失败中学习改进

不需要 MCP、不需要复杂工具，一个 LLM + 记忆 + 循环就能做出基础科研 Agent。后续再逐步添加工具、多 Agent、真实环境集成。

**核心借鉴**：
- ReAct 的循环模式
- Reflexion 的自我反思
- AI Scientist 的科研流程
- Multi-Agent Debate 的协作机制
