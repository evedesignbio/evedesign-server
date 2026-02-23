from evedesign_server.config import Settings

def download_model_files():
    """
    Download all model param files listed in settings
    """
    from urllib.request import urlretrieve
    from os import path, makedirs

    s = Settings()
    print("Downloading files to:", s.model_param_root)

    # iterate all models
    for model_key, model in s.models.items():
        # iterate all model variants
        for model_variant_key, model_variant in model.variants.items():
            # iterate all file arguments (if present)
            if model_variant.file_args is None:
                continue

            for file_arg_name, file_arg_url in model_variant.file_args.items():
                print(model_key, model_variant_key, "...", file_arg_name, "->", file_arg_url)
                if isinstance(file_arg_url, str):
                    output_dir = path.join(s.model_param_root, model_key, model_variant_key)
                    output_file_path = path.join(output_dir, file_arg_name)
                    makedirs(output_dir, exist_ok=True)
                    # download the parameter file
                    urlretrieve(file_arg_url, filename=output_file_path)
                elif isinstance(file_arg_url, list):
                    output_dir = path.join(
                        s.model_param_root, model_key, model_variant_key, file_arg_name
                    )
                    makedirs(output_dir, exist_ok=True)

                    # list of tuples filename, URL for specifying directories with files
                    for (list_file_name, list_file_url) in file_arg_url:
                        output_file_path = path.join(output_dir, list_file_name)
                        # download the parameter file
                        urlretrieve(list_file_url, filename=output_file_path)
