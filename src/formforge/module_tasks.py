"""Domain task registration and common lifecycle callbacks, without engine logic."""
from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class ModuleTaskContext:
    job: dict
    phase: Callable[[str],None]
    cancel: Callable[[],None]
    publish: Callable[...,None]
    register_candidate: Callable[[dict],None]


class ModuleTasks:
    def __init__(self):self.handlers={}
    def register(self,kind,handler):
        if kind in self.handlers:raise ValueError('Duplicate task handler: '+kind)
        self.handlers[kind]=handler
    def get(self,kind):return self.handlers.get(kind)
