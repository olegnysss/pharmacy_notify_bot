from aiogram.filters.callback_data import CallbackData


class OnboardingCallback(CallbackData, prefix="onboarding"):
    action: str


class NavigationCallback(CallbackData, prefix="navigation"):
    action: str


class SubscriptionCallback(CallbackData, prefix="subscription"):
    action: str


class ProductCallback(CallbackData, prefix="product"):
    action: str
    generation: int
    value: int


class SetupCallback(CallbackData, prefix="setup"):
    action: str
    generation: int
    value: int


class SubscriptionViewCallback(CallbackData, prefix="subview"):
    action: str
    subscription_id: int
    page: int
    filter_code: int
    version: int
