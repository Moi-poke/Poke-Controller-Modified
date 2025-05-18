import importlib
import sys
import inspect
import hashlib

import Utility as util


class CommandLoader:
    def __init__(self, base_path: str, base_class: type) -> None:
        self.path = base_path
        self.base_type = base_class
        self.modules: list = []
        self.class_hashes: dict[type, str] = {}  # クラスとそのハッシュ値のマッピング

    def load(self) -> list:
        if not self.modules:  # load if empty
            self.modules = util.importAllModules(self.path)

        # return command class types with their hashes
        return self.getCommandClasses()

    def reload(self) -> list:
        loaded_module_dic = {mod.__name__: mod for mod in self.modules}
        cur_module_names = util.getModuleNames(self.path)

        # Load only not loaded modules
        not_loaded_module_names = list(
            set(cur_module_names) - set(loaded_module_dic.keys())
        )
        if len(not_loaded_module_names) > 0:
            self.modules.extend(
                util.importAllModules(self.path, not_loaded_module_names)
            )

        # Reload commands except deleted ones
        for mod_name in list(set(cur_module_names) & set(loaded_module_dic.keys())):
            importlib.reload(loaded_module_dic[mod_name])

        # Unload deleted commands
        for mod_name in list(set(loaded_module_dic.keys()) - set(cur_module_names)):
            self.modules.remove(loaded_module_dic[mod_name])
            sys.modules.pop(
                loaded_module_dic[mod_name].__name__
            )  # Un-import module forcefully

        # return command class types with their hashes
        return self.getCommandClasses()

    def _generate_class_hash(self, class_type: type) -> str | None:
        """
        クラスの一意識別子を生成する
        ファイルパスとクラス名を組み合わせてハッシュ値を生成
        """
        try:
            file_path = inspect.getfile(class_type)
            class_name = class_type.__name__
            # ファイルパスとクラス名を組み合わせて一意の文字列を作成
            unique_string = f"{file_path}::{class_name}"
            # SHA-256ハッシュを計算
            hash_obj = hashlib.sha256(unique_string.encode())
            return hash_obj.hexdigest()
        except Exception as e:
            # エラーが発生した場合はNoneを返す
            print(f"Error generating hash for class {class_type.__name__}: {e}")
            return None

    def getCommandClasses(self) -> list:
        classes = []
        self.class_hashes.clear()  # ハッシュ値のマッピングをクリア

        for mod in self.modules:
            for c in util.getClassesInModule(mod):
                if issubclass(c, self.base_type) and hasattr(c, "NAME") and c.NAME:
                    # クラスの一意識別子を生成
                    hash_value = self._generate_class_hash(c)
                    if hash_value:
                        self.class_hashes[c] = hash_value
                        classes.append(c)

        return classes

    def get_hash_for_class(self, class_type: type) -> str | None:
        """指定されたクラスのハッシュ値を取得する"""
        if class_type not in self.class_hashes:
            # ハッシュ値が存在しない場合は生成を試みる
            hash_value = self._generate_class_hash(class_type)
            if hash_value:
                self.class_hashes[class_type] = hash_value
        return self.class_hashes.get(class_type)
