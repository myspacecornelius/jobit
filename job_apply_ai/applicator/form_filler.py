"""Generic Selenium form-filling primitives.

Kept applicator-agnostic so the Easy Apply driver, external ATS fillers,
and any future scrapers can share them. The idea: given a root element,
walk its input-ish descendants, figure out the human-visible label, and
route to the right fill strategy (text / select / radio / checkbox / file).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)

# Answer resolver: question text -> answer string (or None to skip).
AnswerResolver = Callable[[str], Optional[str]]


@dataclass
class FieldFillResult:
    label: str
    answer: Optional[str]
    filled: bool
    skipped_reason: str = ""


def click_first_available(driver, selectors: List[str], timeout: float = 5.0) -> bool:
    """Click the first matching selector. Returns True if something was clicked."""
    for selector in selectors:
        try:
            wait = WebDriverWait(driver, timeout)
            el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            el.click()
            logger.debug("Clicked %s", selector)
            return True
        except (TimeoutException, ElementNotInteractableException, NoSuchElementException):
            continue
    return False


def find_label_for(driver, element: WebElement) -> str:
    """Best-effort: figure out the human-readable label for an input."""
    try:
        label_attr = element.get_attribute("aria-label")
        if label_attr:
            return label_attr.strip()

        input_id = element.get_attribute("id")
        if input_id:
            try:
                lbl = driver.find_element(By.CSS_SELECTOR, f"label[for='{input_id}']")
                return lbl.text.strip()
            except NoSuchElementException:
                pass

        # Walk up a few ancestors looking for a label or legend.
        ancestor = element
        for _ in range(4):
            try:
                ancestor = ancestor.find_element(By.XPATH, "..")
            except (NoSuchElementException, StaleElementReferenceException):
                break
            for tag in ("label", "legend", "span", "div"):
                try:
                    nested = ancestor.find_element(By.TAG_NAME, tag)
                    text = (nested.text or "").strip()
                    if text and len(text) < 300:
                        return text
                except NoSuchElementException:
                    continue
    except StaleElementReferenceException:
        return ""
    return ""


def _fill_text(element: WebElement, answer: str) -> None:
    element.clear()
    element.send_keys(answer)


def _fill_select(element: WebElement, answer: str) -> bool:
    select = Select(element)
    options = [(opt.text or "").strip() for opt in select.options]
    target = answer.lower()
    for i, text in enumerate(options):
        if text.lower() == target or target in text.lower():
            select.select_by_index(i)
            return True
    return False


def _fill_radio_group(driver, element: WebElement, answer: str) -> bool:
    """Click the radio whose label best matches `answer`."""
    try:
        container = element.find_element(By.XPATH, "./ancestor::fieldset[1]")
    except NoSuchElementException:
        container = element.find_element(By.XPATH, "./ancestor::*[self::div or self::section][1]")

    radios = container.find_elements(By.CSS_SELECTOR, "input[type='radio']")
    for radio in radios:
        label = find_label_for(driver, radio).lower()
        if answer.lower() in label or label in answer.lower():
            try:
                radio.click()
                return True
            except ElementNotInteractableException:
                driver.execute_script("arguments[0].click();", radio)
                return True
    return False


def fill_field(
    driver,
    element: WebElement,
    resolve: AnswerResolver,
) -> FieldFillResult:
    """Detect the field type and fill it with the answer from `resolve`.

    `resolve` takes the label string and returns the answer (or None).
    """
    try:
        tag = element.tag_name.lower()
        field_type = (element.get_attribute("type") or "").lower()
    except StaleElementReferenceException:
        return FieldFillResult(label="", answer=None, filled=False, skipped_reason="stale")

    label = find_label_for(driver, element)
    if not label:
        return FieldFillResult(label="", answer=None, filled=False, skipped_reason="no_label")

    if field_type == "file":
        answer = resolve(label)
        if not answer:
            return FieldFillResult(label=label, answer=None, filled=False, skipped_reason="no_file")
        file_path = Path(answer).expanduser().resolve()
        if not file_path.is_file():
            return FieldFillResult(
                label=label, answer=answer, filled=False,
                skipped_reason=f"file_missing:{file_path}",
            )
        element.send_keys(str(file_path))
        return FieldFillResult(label=label, answer=str(file_path), filled=True)

    answer = resolve(label)
    if answer is None:
        return FieldFillResult(label=label, answer=None, filled=False, skipped_reason="unknown")

    try:
        if tag == "select":
            ok = _fill_select(element, answer)
            return FieldFillResult(
                label=label, answer=answer, filled=ok,
                skipped_reason="" if ok else "no_matching_option",
            )

        if field_type == "radio":
            ok = _fill_radio_group(driver, element, answer)
            return FieldFillResult(
                label=label, answer=answer, filled=ok,
                skipped_reason="" if ok else "no_matching_radio",
            )

        if field_type == "checkbox":
            expected = answer.strip().lower() in ("yes", "true", "1", "on")
            if element.is_selected() != expected:
                try:
                    element.click()
                except ElementNotInteractableException:
                    driver.execute_script("arguments[0].click();", element)
            return FieldFillResult(label=label, answer=answer, filled=True)

        _fill_text(element, answer)
        return FieldFillResult(label=label, answer=answer, filled=True)

    except (ElementNotInteractableException, StaleElementReferenceException) as exc:
        return FieldFillResult(
            label=label, answer=answer, filled=False,
            skipped_reason=f"interact_error:{exc.__class__.__name__}",
        )


def fill_visible_fields(driver, resolve: AnswerResolver) -> List[FieldFillResult]:
    """Fill every text/select/radio/checkbox/file input currently in the DOM."""
    selectors = [
        "input[type='text']",
        "input[type='email']",
        "input[type='tel']",
        "input[type='number']",
        "input[type='url']",
        "input[type='file']",
        "input[type='radio']",
        "input[type='checkbox']",
        "textarea",
        "select",
    ]
    results: List[FieldFillResult] = []
    seen: set = set()
    for sel in selectors:
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            try:
                if not el.is_displayed():
                    continue
                key = el.id
                if key in seen:
                    continue
                seen.add(key)
                results.append(fill_field(driver, el, resolve))
            except StaleElementReferenceException:
                continue
    return results
