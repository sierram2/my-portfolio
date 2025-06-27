# -*- coding: utf-8 -*-
"""
Created on Thu Jun 26 11:50:44 2025

@author: sierram2
"""
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load the 'mtcars' dataset from seaborn's repository
url = 'https://raw.githubusercontent.com/selva86/datasets/master/mtcars.csv'
df = pd.read_csv(url)

# Basic scatter plot: Horsepower vs. MPG
plt.figure(figsize=(8, 5))
sns.scatterplot(data=df, x='hp', y='mpg', hue='cyl', palette='viridis')

# Add labels and title
plt.title('Horsepower vs. MPG')
plt.xlabel('Horsepower (hp)')
plt.ylabel('Miles per Gallon (mpg)')

# Show plot
plt.tight_layout()
plt.show()