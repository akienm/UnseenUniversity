from collections import OrderedDict

import inspect
import logging
import os
import pdb
import sys

# === SmartDict ===

class SmartDict(OrderedDict):
    def bannerize(self, title=None):
        return Diagnostics._bannerize(self, title)

    def show(self, title=None):
        print(self.bannerize(title))


# === Diagnostics Logger ===

class Diagnostics(logging.Logger):
    def __init__(self, name):
        super().__init__(name, level=logging.DEBUG)
        self._setup_handlers()

    def _setup_handlers(self):
        script_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
        log_filename = f"{script_name}.log"

        file_handler = logging.FileHandler(log_filename, mode='w', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        file_handler.setFormatter(file_formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
        console_handler.setFormatter(console_formatter)

        self.addHandler(file_handler)
        self.addHandler(console_handler)

    def _maybe_bannerize(self, obj):
        if isinstance(obj, (dict, list, SmartDict)):
            return "\n" + self._bannerize(obj)
        return obj

    def _format_context(self, obj=None):
        import inspect

        if obj:
            class_name = type(obj).__name__
            instance_name = getattr(obj, "get_name", lambda: None)() or getattr(obj, "name", None) or "?"
            func_name = inspect.currentframe().f_back.f_back.f_code.co_name
            return f"{class_name}.{func_name}({instance_name})"

        frame = inspect.currentframe()
        for _ in range(3):
            if frame:
                frame = frame.f_back

        if frame:
            local_vars = frame.f_locals
            func_name = frame.f_code.co_name
            if 'self' in local_vars:
                obj = local_vars['self']
                class_name = type(obj).__name__
                instance_name = getattr(obj, "get_name", lambda: None)() or getattr(obj, "name", None) or "?"
                return f"{class_name}.{func_name}({instance_name})"
            elif 'cls' in local_vars:
                cls = local_vars['cls']
                class_name = cls.__name__
                return f"{class_name}.{func_name}(class)"
            else:
                return f"{func_name}(function)"
        return "unknown.unknown(?)"



    def _log_with_context(self, level, msg, *args, obj=None, **kwargs):
        context = self._format_context(obj)
        msg = self._maybe_bannerize(msg)
        super().log(level, f":::{context} {msg}", *args, **kwargs)

    def debug(self, msg, *args, obj=None, **kwargs):
        self._log_with_context(logging.DEBUG, msg, *args, obj=obj, **kwargs)

    def info(self, msg, *args, obj=None, **kwargs):
        self._log_with_context(logging.INFO, msg, *args, obj=obj, **kwargs)

    def warning(self, msg, *args, obj=None, **kwargs):
        self._log_with_context(logging.WARNING, msg, *args, obj=obj, **kwargs)

    def error(self, msg, *args, obj=None, **kwargs):
        self._log_with_context(logging.ERROR, msg, *args, obj=obj, **kwargs)

    def critical(self, msg, *args, obj=None, **kwargs):
        self._log_with_context(logging.CRITICAL, msg, *args, obj=obj, **kwargs)

    def break_here(self):
        """Drop into the caller's frame."""
        frame = inspect.currentframe().f_back
        pdb.Pdb().set_trace(frame)

    @staticmethod
    def _bannerize(data, title=None):
        ID = 'ID'
        OBJECT_ALREADY_DISPLAYED = '*** OBJECT ALREADY DISPLAYED ABOVE ***'
        indents = 4

        class Bannerize:
            def __init__(self):
                self.indent = 0
                self.final_result = ''
                self.indent_char = ' '
                self.list_of_completed_objects = []
                self.types_to_treat_as_string = (bool, str, int, tuple, float)

            def ind(self, data):
                return (self.indent * self.indent_char) + data

            def check_for_dupes(self, item):
                if item is not None and not isinstance(item, self.types_to_treat_as_string):
                    item_id_string = ""
                    if isinstance(item, dict) and ID in item:
                        item_id_string += item[ID]
                    elif hasattr(item, "get_name"):
                        item_id_string += item.get_name()
                    else:
                        item_id_string += f"{item}"
                    item_id_string += f" with OID of {id(item)}"
                    if item_id_string in self.list_of_completed_objects:
                        return f'{OBJECT_ALREADY_DISPLAYED} as {item_id_string}'
                    self.list_of_completed_objects.append(item_id_string)
                return item

            def bannerize(self, data=None, iteration=0):
                iteration += 1
                if isinstance(data, dict):
                    self.final_result += '{\n'
                    self.indent += indents
                    for key, item in data.items():
                        self.final_result += self.ind(f'"{key}": ')
                        item = self.check_for_dupes(item)
                        self.bannerize(data=item, iteration=iteration)
                    self.indent -= indents
                    self.final_result += self.ind('}\n')
                elif isinstance(data, list):
                    self.final_result += '[\n'
                    self.indent += indents
                    for item in data:
                        item = self.check_for_dupes(item)
                        self.final_result += self.ind('')
                        self.bannerize(data=item, iteration=iteration)
                    self.indent -= indents
                    self.final_result += self.ind(']\n')
                else:
                    if isinstance(data, str) and data != OBJECT_ALREADY_DISPLAYED:
                        data = f'"{data}"'
                    self.final_result += str(data) + '\n'

                if iteration > 20:
                    raise Exception(f"infinite loop in bannerizer: {data}")

        if title:
            data = {title: data}
        b = Bannerize()
        b.bannerize(data)
        return b.final_result

    @classmethod
    def requests_response_to_smartdict(cls, response):
        sd = SmartDict()
        sd["url"] = response.url
        sd["status_code"] = response.status_code
        sd["headers"] = SmartDict(response.headers)
        sd["reason"] = response.reason
        sd["elapsed"] = str(response.elapsed)
        sd["encoding"] = response.encoding
        sd["ok"] = response.ok
        sd["history"] = [str(r) for r in response.history]
        sd["cookies"] = SmartDict(response.cookies.get_dict())
        try:
            sd["json"] = SmartDict(response.json())
        except Exception as e:
            sd["text"] = response.text[:1000]  # truncate for safety
            sd["json_error"] = str(e)
        return sd

# === Logger Factory ===

def get_logger(name="diagnostics"):
    logging.setLoggerClass(Diagnostics)
    return logging.getLogger(name)
