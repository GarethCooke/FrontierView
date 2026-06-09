from agent.eval.questions.types import Question
from agent.eval.questions.generated import GENERATED_QUESTIONS
from agent.eval.questions.curated import CURATED_QUESTIONS

ALL_QUESTIONS: list[Question] = GENERATED_QUESTIONS + CURATED_QUESTIONS

__all__ = ["Question", "GENERATED_QUESTIONS", "CURATED_QUESTIONS", "ALL_QUESTIONS"]
