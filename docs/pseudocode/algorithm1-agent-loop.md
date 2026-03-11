# Algorithm 1: LLM Agent Tool-Use Loop

Core orchestration algorithm. The LLM autonomously decides which tools
to invoke, iterating until it produces a final answer or reaches the
iteration limit.

Reference: `modules/llm.py:run_agent()`, `modules/helper.py:call_llm_with_tools()`

```
Algorithm 1: LLM Agent Tool-Use Loop
----------------------------------------------------------------------
Input : question (natural-language research question),
        model (LLM identifier),
        cell_size (grid cell size in meters),
        max_iter (maximum tool-use iterations, default 10)
Output: AgentResult (answer, tool_calls[], metrics)

 1  resolved <- ResolveTerms(question)           // Algorithm 4
 2  prompt   <- RenderTemplate("agent_system.jinja2",
                  concepts, resolved, cell_size)
 3  user_msg <- question + FormatResolvedTerms(resolved)
 4  messages <- [{role: system, content: prompt},
                 {role: user,   content: user_msg}]
 5  result   <- new AgentResult(model)
 6  t_start  <- CurrentTime()

 7  for i <- 1 to max_iter do
 8      response <- LLM.ChatCompletion(model, messages, TOOL_SCHEMAS)
 9      AccumulateTokens(result, response.usage)

10      if response.finish_reason = "stop" then
11          result.answer <- response.content
12          break

13      for each tc in response.tool_calls do
14          args <- ParseJSON(tc.arguments)
15          switch tc.name
16              case "run_cypher_query":
17                  (text, record) <- HandleCypherQuery(args)
                                                      // Algorithm 3
18              case "run_spatial_analysis":
19                  (text, record) <- HandleSpatialAnalysis(args)
                                                      // Algorithm 6
20              default:
21                  (text, record) <- ("Unknown tool", ErrorRecord)
22          end switch
23          Append(result.tool_calls, record)
24          Append(messages, {role: tool, id: tc.id, content: text})
25      end for
26  end for

27  result.duration <- CurrentTime() - t_start
28  result.cost     <- CalculateCost(model, result.tokens)
29  return result
```

## Complexity

- Outer loop: O(max_iter) LLM API calls
- Each iteration may invoke 1..n tool calls (typically 1)
- Total LLM calls: at most max_iter (default 10)
- Dominant cost: LLM inference latency + Neo4j query time + subprocess execution
