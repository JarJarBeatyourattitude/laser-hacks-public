#!/usr/bin/env python3
import pathlib

def gather_files_content(output_file: pathlib.Path, extensions: tuple) -> None:
    """
    Recursively gather the content of files with specified extensions in the current directory 
    and its subdirectories and save it to a text file.

    This function searches for files that have an extension matching any in the provided tuple.
    It writes a header with the file's relative path and then writes the file's content 
    to the output file. Each file's content is separated by a line of equal signs.
    
    Args:
        output_file (pathlib.Path): The file path to which the output should be written.
        extensions (tuple): A tuple of file extensions to search for (e.g., (".py", ".html")).
    
    Returns:
        None
    """
    current_path = pathlib.Path(".")
    with output_file.open("w", encoding="utf-8") as out_file:
        for file_path in current_path.rglob("*"):
            if file_path.suffix in extensions:
                out_file.write(f"File: {file_path}\n\n")
                try:
                    with file_path.open("r", encoding="utf-8") as file:
                        content = file.read()
                        out_file.write(content)
                except Exception as e:
                    out_file.write(f"Error reading file {file_path}: {e}")
                out_file.write("\n" + "=" * 80 + "\n\n")

def main() -> None:
    """
    The main entry point of the script.

    Creates an output text file 'files_dump.txt' in the current directory and writes the gathered
    content of all Python and HTML files to it.
    """
    output_path = pathlib.Path("files_dump.txt")
    gather_files_content(output_path, (".py", ".html"))
    print(f"Dump saved to {output_path}")

if __name__ == "__main__":
    main()
