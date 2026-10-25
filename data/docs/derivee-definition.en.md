---
topic: derivative
level: beginner
language: ENGLISH
title: The derivative of a function: definition
status: draft
---
## Definition: the derivative as a limit
<!-- concept: nombre dérivé -->
For a function f and a number a, the difference quotient between a and a + h is (f(a + h) − f(a)) / h. It is the slope of the line through the two points of the graph with x-coordinates a and a + h.

When h gets closer and closer to 0, if this quotient gets closer to a finite number, that number is called the derivative of f at a and is written f'(a): f'(a) = lim (f(a + h) − f(a)) / h as h → 0. Doing this for every x gives a new function f', the derivative of f.

## The tangent line
<!-- concept: tangente -->
The number f'(a) is the slope of the tangent line to the graph of f at the point with x-coordinate a. The tangent line touches the graph at that point and follows its direction there.

The equation of the tangent line at the point with x-coordinate a is y = f'(a)(x − a) + f(a).

## Worked example: f(x) = x²
<!-- concept: nombre dérivé -->
Take f(x) = x². The difference quotient is ((a + h)² − a²) / h = (2ah + h²) / h = 2a + h. When h tends to 0, it tends to 2a, so f'(a) = 2a, and the derivative is f'(x) = 2x.

At a = 3: f(3) = 9 and f'(3) = 6, so the tangent line has slope 6 and equation y = 6(x − 3) + 9, that is y = 6x − 9.
