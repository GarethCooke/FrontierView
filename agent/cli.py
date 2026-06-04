import sys

from agent.loop import run


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m agent.cli \"<question>\"")
        sys.exit(1)
    question = " ".join(sys.argv[1:])
    answer = run(question)
    print(f"\n{'=' * 60}\n{answer}\n{'=' * 60}")


if __name__ == "__main__":
    main()
