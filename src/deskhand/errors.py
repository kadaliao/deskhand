"""Failures the runtime is expected to survive."""


class DeskhandError(RuntimeError):
    """Base class for everything this package raises on purpose."""


class BadChoice(DeskhandError):
    """A decider asked for something the current view does not allow.

    Recoverable: the step is recorded as a failure and the loop keeps going.
    """


class StaleTarget(DeskhandError):
    """The target no longer means what it meant when the decision was made.

    Recoverable: re-observe and let the decider choose again.
    """


class CannotDo(DeskhandError):
    """The backend cannot perform this action at all.

    Recoverable in the same way as :class:`BadChoice` -- one unsupported action
    must not end the run.
    """


class NoPermission(DeskhandError):
    """A macOS permission is missing. Not recoverable without user action."""


class DeciderStuck(DeskhandError):
    """The decider cannot produce a choice at all."""


class ModelFailed(DeskhandError):
    """A model call did not produce something usable.

    Recoverable in the same way as every other decider failure: the step is
    recorded with a reason and fed back into the next decision. A model that is
    down, slow, or answering nonsense must not end a run that one more attempt
    could save -- and a verifier that could not be asked must never be read as a
    confirmation.
    """
